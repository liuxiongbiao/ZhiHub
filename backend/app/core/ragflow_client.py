"""RagFlow 文档解析客户端（PRD 3.2 语义切片）。

双模式设计（与 sms_client/wechat_client/alipay_client 同构）：
  - 配置了 RAGFLOW_BASE_URL + RAGFLOW_DATASET_ID + （API Key 或邮箱密码）时，
    上传文档到 RagFlow 数据集 -> 轮询解析状态 -> 拉取语义切片；
  - 未配置或 RagFlow 不可用时抛 RagflowUnavailable，调用方降级到本地规则解析，
    保证知识库管道在任何部署形态下都可运行。
"""

import json
import logging
import time

import httpx

from app.config import settings
from app.utils.file_parser import Chunk

logger = logging.getLogger(__name__)


class RagflowUnavailable(RuntimeError):
    """RagFlow 未配置或不可用（调用方据此降级本地解析）。"""


class RagflowClient:
    """RagFlow API 客户端（上传文档、轮询状态、拉取切片）。"""

    def __init__(self) -> None:
        self.base_url = (settings.ragflow_base_url or "").rstrip("/")
        self.dataset_id = settings.ragflow_dataset_id or ""
        self.api_key = settings.ragflow_api_key or ""
        self.email = settings.ragflow_email or ""
        self.password = settings.ragflow_password or ""
        self.is_mock = not (
            self.base_url and self.dataset_id and (self.api_key or (self.email and self.password))
        )
        self._token: str | None = None

    def _headers(self) -> dict[str, str]:
        if self.is_mock:
            raise RagflowUnavailable("RagFlow 未配置")
        if self._token is None:
            self._token = self._acquire_token()
        return {"Authorization": f"Bearer {self._token}"}

    def _acquire_token(self) -> str:
        if self.api_key:
            return self.api_key
        resp = httpx.post(
            f"{self.base_url}/api/v1/user/login",
            json={"email": self.email, "password": self.password},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json().get("data") or {}
        token = data.get("access_token")
        if not token:
            raise RagflowUnavailable("RagFlow 登录未返回 access_token")
        return token

    def parse(self, filename: str, content: bytes) -> list[Chunk]:
        """上传文档到 RagFlow 并拉取语义切片。失败抛 RagflowUnavailable。"""
        if self.is_mock:
            raise RagflowUnavailable("RagFlow 未配置")

        headers = self._headers()
        files = {"file": (filename, content)}
        data = {"meta": json.dumps({"filename": filename}, ensure_ascii=False)}
        try:
            resp = httpx.post(
                f"{self.base_url}/api/v1/datasets/{self.dataset_id}/documents",
                headers=headers, files=files, data=data, timeout=60.0,
            )
            resp.raise_for_status()
            doc_id = (resp.json().get("data") or {}).get("id")
            if not doc_id:
                raise RagflowUnavailable(f"RagFlow 上传未返回文档 ID: {resp.text[:200]}")

            deadline = time.time() + settings.ragflow_parse_timeout_seconds
            while True:
                if time.time() > deadline:
                    raise RagflowUnavailable("RagFlow 解析超时")
                status_resp = httpx.get(
                    f"{self.base_url}/api/v1/datasets/{self.dataset_id}/documents/{doc_id}",
                    headers=headers, timeout=30.0,
                )
                status_resp.raise_for_status()
                doc = (status_resp.json().get("data") or {})
                state = (doc.get("status") or "").upper()
                if state == "DONE":
                    break
                if state in ("FAIL", "CANCEL"):
                    raise RagflowUnavailable(
                        f"RagFlow 解析失败: {doc.get('run') or doc.get('progress_msg') or state}"
                    )
                time.sleep(3)

            chunks_resp = httpx.get(
                f"{self.base_url}/api/v1/datasets/{self.dataset_id}/documents/{doc_id}/chunks",
                params={"page": 1, "page_size": 1024},
                headers=headers, timeout=30.0,
            )
            chunks_resp.raise_for_status()
            raw_chunks = (chunks_resp.json().get("data") or {}).get("chunks") or []
        except RagflowUnavailable:
            raise
        except Exception as e:
            logger.warning(f"RagFlow parse request failed: {e}")
            raise RagflowUnavailable(f"RagFlow 请求失败: {e}") from e

        chunks: list[Chunk] = []
        for i, c in enumerate(raw_chunks):
            text = (c.get("content") or "").strip()
            if text:
                chunks.append(Chunk(index=i, text=text, heading=None))
        if not chunks:
            raise RagflowUnavailable("RagFlow 返回 0 个有效切片")
        logger.info(f"RagFlow parsed {filename}: {len(chunks)} chunks")
        return chunks


_ragflow_client: RagflowClient | None = None


def get_ragflow_client() -> RagflowClient:
    """进程级单例（与 sms/wechat/alipay 客户端一致）。"""
    global _ragflow_client
    if _ragflow_client is None:
        _ragflow_client = RagflowClient()
    return _ragflow_client

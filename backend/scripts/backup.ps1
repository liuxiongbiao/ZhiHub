<#
backend/scripts/backup.ps1

职责: 执行 MySQL 每日全量备份、Milvus 每周物理快照与 Elasticsearch 原生快照，
      并按保留天数清理历史运行目录（PRD §4.4）。

用法:
  powershell -File scripts/backup.ps1 -Scope daily -VerifyMySqlRestore
  powershell -File scripts/backup.ps1 -Scope weekly
  powershell -File scripts/backup.ps1 -Scope all -RetentionDays 30
#>

[CmdletBinding()]
param(
    [ValidateSet("daily", "weekly", "all")]
    [string]$Scope = "daily",
    [string]$BackupRoot = "",
    [ValidateRange(1, 3650)]
    [int]$RetentionDays = 30,
    [string]$MySqlContainer = "deploy-mysql-1",
    [string]$MySqlUser = "root",
    [string]$MySqlPassword = "root",
    [string]$MySqlDatabase = "zhihub",
    [string]$MilvusContainer = "deploy-milvus-1",
    [string]$MinioContainer = "deploy-minio-1",
    [string]$EtcdContainer = "deploy-etcd-1",
    [string]$KnowledgeWorkerContainer = "deploy-celery-knowledge-worker-1",
    [string]$ElasticsearchUrl = "http://127.0.0.1:9200",
    [switch]$VerifyMySqlRestore
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($BackupRoot)) {
    $BackupRoot = Join-Path $PSScriptRoot "..\..\backups"
}

function Invoke-Docker {
    # 非零退出码立即终止；错误中不回显参数，避免泄露凭据。
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & docker @Arguments | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker 命令执行失败（exit=$LASTEXITCODE）。"
    }
}

function Test-ContainerExists {
    # 周备前解析所有精确容器名，避免误操作未知目标。
    param([Parameter(Mandatory = $true)][string]$Name)
    & docker inspect $Name *> $null
    return $LASTEXITCODE -eq 0
}

function New-BackupDirectory {
    # 时间戳目录把每轮产物隔离，便于校验、恢复和安全清理。
    New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
    $resolvedRoot = (Resolve-Path -LiteralPath $BackupRoot).Path
    $driveRoot = [System.IO.Path]::GetPathRoot($resolvedRoot)
    if ($resolvedRoot -eq $driveRoot) {
        throw "BackupRoot 不允许指向磁盘根目录。"
    }
    $runDirectory = Join-Path $resolvedRoot ("run-{0:yyyyMMdd-HHmmss}" -f (Get-Date))
    New-Item -ItemType Directory -Path $runDirectory -Force | Out-Null
    return (Resolve-Path -LiteralPath $runDirectory).Path
}

function Backup-MySql {
    # dump 在容器内生成，避免 PowerShell 管道改变 SQL 文件编码。
    param([Parameter(Mandatory = $true)][string]$RunDirectory)
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $fileName = "mysql-$MySqlDatabase-$stamp.sql"
    $containerFile = "/tmp/$fileName"
    $hostFile = Join-Path $RunDirectory $fileName
    try {
        Invoke-Docker -Arguments @(
            "exec", "-e", "MYSQL_PWD=$MySqlPassword", $MySqlContainer,
            "mysqldump", "-u", $MySqlUser, "--single-transaction", "--routines",
            "--triggers", "--events", "--set-gtid-purged=OFF",
            "--result-file=$containerFile", $MySqlDatabase
        )
        Invoke-Docker -Arguments @("cp", "${MySqlContainer}:$containerFile", $hostFile)
    }
    finally {
        & docker exec $MySqlContainer rm -f $containerFile *> $null
    }
    if (-not (Test-Path -LiteralPath $hostFile) -or (Get-Item -LiteralPath $hostFile).Length -eq 0) {
        throw "MySQL dump 未产出或为空：$hostFile"
    }
    return $hostFile
}

function Test-MySqlRestore {
    # 随机临时库验证真实可恢复性，finally 保证验证数据不残留。
    param([Parameter(Mandatory = $true)][string]$DumpFile)
    $suffix = Get-Date -Format "yyyyMMddHHmmss"
    $verifyDatabase = "zhihub_restore_verify_$suffix"
    $containerFile = "/tmp/$([System.IO.Path]::GetFileName($DumpFile))"
    try {
        Invoke-Docker -Arguments @("cp", $DumpFile, "${MySqlContainer}:$containerFile")
        Invoke-Docker -Arguments @(
            "exec", "-e", "MYSQL_PWD=$MySqlPassword", $MySqlContainer,
            "mysql", "-u", $MySqlUser, "-e",
            "CREATE DATABASE $verifyDatabase CHARACTER SET utf8mb4;"
        )
        Invoke-Docker -Arguments @(
            "exec", "-e", "MYSQL_PWD=$MySqlPassword", $MySqlContainer,
            "mysql", "-u", $MySqlUser, $verifyDatabase, "-e", "source $containerFile"
        )
        $tableCount = & docker exec -e "MYSQL_PWD=$MySqlPassword" $MySqlContainer `
            mysql -N -s -u $MySqlUser -e `
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$verifyDatabase';"
        if ($LASTEXITCODE -ne 0 -or [int]$tableCount -lt 1) {
            throw "恢复验证失败：临时库没有恢复出业务表。"
        }
        return [int]$tableCount
    }
    finally {
        & docker exec -e "MYSQL_PWD=$MySqlPassword" $MySqlContainer `
            mysql -u $MySqlUser -e "DROP DATABASE IF EXISTS $verifyDatabase;" *> $null
        & docker exec $MySqlContainer rm -f $containerFile *> $null
    }
}

function Backup-Milvus {
    # 暂停写入端与 Milvus 后同步复制三份状态，避免跨组件时间点不一致。
    param([Parameter(Mandatory = $true)][string]$RunDirectory)
    $targets = @($MilvusContainer, $MinioContainer, $EtcdContainer, $KnowledgeWorkerContainer)
    foreach ($target in $targets) {
        if (-not (Test-ContainerExists -Name $target)) {
            throw "Milvus 周备所需容器不存在：$target"
        }
    }
    $staging = Join-Path $RunDirectory "milvus-staging"
    New-Item -ItemType Directory -Path $staging -Force | Out-Null
    $paused = @()
    try {
        foreach ($target in @($KnowledgeWorkerContainer, $MilvusContainer)) {
            Invoke-Docker -Arguments @("pause", $target)
            $paused += $target
        }
        Invoke-Docker -Arguments @("cp", "${MilvusContainer}:/var/lib/milvus", (Join-Path $staging "milvus"))
        Invoke-Docker -Arguments @("cp", "${MinioContainer}:/minio_data", (Join-Path $staging "minio_data"))
        Invoke-Docker -Arguments @("cp", "${EtcdContainer}:/etcd", (Join-Path $staging "etcd"))
    }
    finally {
        for ($index = $paused.Count - 1; $index -ge 0; $index--) {
            & docker unpause $paused[$index] *> $null
        }
    }
    $archive = Join-Path $RunDirectory ("milvus-physical-{0}.tar.gz" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
    & tar -czf $archive -C $staging .
    if ($LASTEXITCODE -ne 0) {
        throw "Milvus 快照压缩失败。"
    }
    $resolvedRun = (Resolve-Path -LiteralPath $RunDirectory).Path
    $resolvedStaging = (Resolve-Path -LiteralPath $staging).Path
    if (-not $resolvedStaging.StartsWith($resolvedRun, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝清理不在本轮备份目录内的暂存路径。"
    }
    Remove-Item -LiteralPath $resolvedStaging -Recurse -Force
    return $archive
}

function Backup-Elasticsearch {
    # 原生 Snapshot API 保留索引元数据，比直接复制 data 目录更适合恢复。
    $repositoryBody = @{ type = "fs"; settings = @{ location = "/usr/share/elasticsearch/snapshots"; compress = $true } } | ConvertTo-Json -Depth 4
    Invoke-RestMethod -Method Put -Uri "$ElasticsearchUrl/_snapshot/zhihub_fs" `
        -ContentType "application/json" -Body $repositoryBody | Out-Null
    $snapshotName = "zhihub-{0}" -f (Get-Date -Format "yyyyMMdd-HHmmss")
    $result = Invoke-RestMethod -Method Put `
        -Uri "$ElasticsearchUrl/_snapshot/zhihub_fs/$snapshotName`?wait_for_completion=true" `
        -ContentType "application/json" -Body '{"indices":"*","include_global_state":true}'
    if ($result.snapshot.state -ne "SUCCESS") {
        throw "Elasticsearch 快照失败：$($result.snapshot.state)"
    }
    return $snapshotName
}

function Remove-ExpiredElasticsearchSnapshots {
    # Elasticsearch 快照只能经 Snapshot API 删除，直接删仓库文件会损坏仓库元数据。
    $cutoff = (Get-Date).ToUniversalTime().AddDays(-$RetentionDays)
    $result = Invoke-RestMethod -Method Get -Uri "$ElasticsearchUrl/_snapshot/zhihub_fs/_all"
    foreach ($snapshot in $result.snapshots) {
        $endedAt = [DateTimeOffset]::Parse($snapshot.end_time).UtcDateTime
        if ($snapshot.snapshot -match '^zhihub-\d{8}-\d{6}$' -and $endedAt -lt $cutoff) {
            Invoke-RestMethod -Method Delete `
                -Uri "$ElasticsearchUrl/_snapshot/zhihub_fs/$($snapshot.snapshot)" | Out-Null
        }
    }
}

function Remove-ExpiredBackups {
    # 只删除根目录内匹配固定命名的历史轮次，避免宽泛递归删除。
    $resolvedRoot = (Resolve-Path -LiteralPath $BackupRoot).Path
    $cutoff = (Get-Date).AddDays(-$RetentionDays)
    Get-ChildItem -LiteralPath $resolvedRoot -Directory -Filter "run-*" | Where-Object {
        $_.Name -match '^run-\d{8}-\d{6}$' -and $_.LastWriteTime -lt $cutoff
    } | ForEach-Object {
        $resolvedTarget = (Resolve-Path -LiteralPath $_.FullName).Path
        if (-not $resolvedTarget.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "拒绝清理 BackupRoot 之外的路径：$resolvedTarget"
        }
        Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
    }
}

$runDirectory = New-BackupDirectory
$manifest = [ordered]@{
    created_at = (Get-Date).ToUniversalTime().ToString("o")
    scope = $Scope
    mysql_dump = $null
    mysql_sha256 = $null
    mysql_restore_table_count = $null
    milvus_snapshot = $null
    milvus_sha256 = $null
    elasticsearch_snapshot = $null
}

try {
    if ($Scope -in @("daily", "all")) {
        $dump = Backup-MySql -RunDirectory $runDirectory
        $manifest.mysql_dump = [System.IO.Path]::GetFileName($dump)
        $manifest.mysql_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $dump).Hash
        if ($VerifyMySqlRestore) {
            $manifest.mysql_restore_table_count = Test-MySqlRestore -DumpFile $dump
        }
    }
    if ($Scope -in @("weekly", "all")) {
        $milvusArchive = Backup-Milvus -RunDirectory $runDirectory
        $manifest.milvus_snapshot = [System.IO.Path]::GetFileName($milvusArchive)
        $manifest.milvus_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $milvusArchive).Hash
        $manifest.elasticsearch_snapshot = Backup-Elasticsearch
        Remove-ExpiredElasticsearchSnapshots
    }
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $runDirectory "manifest.json") -Encoding UTF8
    Remove-ExpiredBackups
    Write-Output "BACKUP_SUCCESS=$runDirectory"
    Write-Output ($manifest | ConvertTo-Json -Compress)
}
catch {
    Write-Error "备份失败：$($_.Exception.Message)"
    throw
}

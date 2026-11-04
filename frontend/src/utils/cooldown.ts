/** 验证码按钮的 60 秒倒计时。减到 0 后停止。 */
export function startCooldown(target: { value: number }, seconds = 60) {
  target.value = seconds
  const timer = window.setInterval(() => {
    target.value -= 1
    if (target.value <= 0) window.clearInterval(timer)
  }, 1000)
}

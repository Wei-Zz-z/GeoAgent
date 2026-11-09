<template>
  <div class="login-gate">
    <section class="login-card">
      <h1 class="login-title">土地变化智能问答</h1>
      <p class="login-sub">省域平台 2.0 · 浙政钉统一用户体系</p>

      <p v-if="auth.status === 'checking'" class="login-state">正在校验省域平台身份…</p>
      <p v-else-if="auth.error" class="login-state login-error">{{ auth.error }}</p>
      <p v-else class="login-state">请从省域平台场景页进入本系统。</p>

      <p v-if="auth.mode === 'ticket'" class="login-hint">
        入口地址由省域平台携带票据跳转生成，票据一次性且 2 分钟内有效；
        若提示票据失效，请回到平台重新进入本场景。
      </p>

      <div class="login-actions">
        <button class="btn" type="button" :disabled="auth.status === 'checking'" @click="retry">
          重新校验
        </button>
        <a
          v-if="auth.mode === 'ticket'"
          class="login-link"
          href="https://kjzl.zrzyt.zj.gov.cn/kjzlptv2/ticketTest"
          target="_blank"
          rel="noopener"
        >
          平台 ticket 测试页
        </a>
      </div>
    </section>
  </div>
</template>

<script setup>
import { auth } from '../auth'

function retry() {
  auth.ensure()
}
</script>

<style scoped>
.login-gate {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: 24px;
  background: var(--bg, #f5f6f8);
}

.login-card {
  width: 100%;
  max-width: 420px;
  padding: 32px 28px;
  border-radius: 14px;
  background: #fff;
  box-shadow: 0 10px 30px rgba(15, 23, 42, 0.08);
  text-align: center;
}

.login-title {
  margin: 0 0 6px;
  font-size: 20px;
  font-weight: 600;
}

.login-sub {
  margin: 0 0 22px;
  font-size: 12px;
  color: #8a93a3;
}

.login-state {
  margin: 0 0 12px;
  font-size: 14px;
  line-height: 1.7;
  color: #2f3640;
}

.login-error {
  color: #c0392b;
}

.login-hint {
  margin: 0 0 20px;
  font-size: 12px;
  line-height: 1.7;
  color: #8a93a3;
}

.login-actions {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: center;
}

.login-link {
  font-size: 12px;
  color: #4a6fa5;
  text-decoration: none;
}

.login-link:hover {
  text-decoration: underline;
}
</style>

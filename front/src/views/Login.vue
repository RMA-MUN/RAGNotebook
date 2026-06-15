<template>
  <div class="login-page">
    <van-nav-bar
      title="用户登录"
      left-arrow
      @click-left="onClickLeft"
      fixed
    />
    
    <div class="login-container">
      <div class="login-logo">
        <div class="logo-mark">
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 2a2 2 0 0 1 2 2c0 .74-.4 1.39-1 1.73V7h1a7 7 0 0 1 7 7h1a1 1 0 0 1 1 1v3a1 1 0 0 1-1 1h-1.27A7.01 7.01 0 0 1 14 23h-4a7.01 7.01 0 0 1-6.73-5H2a1 1 0 0 1-1-1v-3a1 1 0 0 1 1-1h1a7 7 0 0 1 7-7h1V5.73c-.6-.34-1-.99-1-1.73a2 2 0 0 1 2-2z"/>
          </svg>
        </div>
        <h2>RAG 智能问答</h2>
      </div>
      
      <van-form @submit="onSubmit" class="login-form">
        <van-cell-group inset>
          <van-field
            v-model="username"
            name="username"
            label="用户名"
            placeholder="请输入用户名"
            :rules="[{ required: true, message: '请填写用户名' }]"
          />
          <van-field
            v-model="password"
            type="password"
            name="password"
            label="密码"
            placeholder="请输入密码"
            :rules="[{ required: true, message: '请填写密码' }]"
          />
        </van-cell-group>
        
        <div class="submit-btn">
          <van-button round block type="primary" native-type="submit" size="large">
            登录
          </van-button>
        </div>
        
        <div class="test-user-btn">
          <van-button round block type="default" size="large" @click="loginTestUser">
            测试用户登录
          </van-button>
        </div>
        
        <div class="register-link">
          还没有账号？<span @click="goToRegister">去注册</span>
        </div>
      </van-form>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue';
import { useRouter } from 'vue-router';
import { showToast } from 'vant';
import { useUserStore } from '../store/user';

const router = useRouter();
const userStore = useUserStore();

const username = ref('');
const password = ref('');

const onSubmit = async (values) => {
  // 显示加载提示
  showToast({
    type: 'loading',
    message: '登录中...',
    forbidClick: true,
    duration: 0
  });
  
  try {
    // 调用API登录
    const result = await userStore.login({
      username: username.value,
      password: password.value
    });
    
    if (result.success) {
      showToast({
        type: 'success',
        message: result.message
      });
      
      router.push('/');
    } else {
      showToast({
        type: 'fail',
        message: result.message
      });
    }
  } catch (error) {
    showToast({
      type: 'fail',
      message: '登录失败，请稍后再试'
    });
  }
};

const onClickLeft = () => {
  router.back();
};

const goToRegister = () => {
  router.push('/register');
};

const loginTestUser = async () => {
  username.value = 'test';
  password.value = '666666';

  showToast({
    type: 'loading',
    message: '测试用户登录中...',
    forbidClick: true,
    duration: 0
  });

  try {
    const result = await userStore.login({
      username: username.value,
      password: password.value
    });

    if (result.success) {
      showToast({
        type: 'success',
        message: result.message
      });
      router.push('/');
    } else {
      showToast({
        type: 'fail',
        message: result.message
      });
    }
  } catch (error) {
    showToast({
      type: 'fail',
      message: '登录失败，请稍后再试'
    });
  }
};
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  background-color: var(--color-bg);
}

.login-container {
  padding-top: 56px;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.login-logo {
  margin: 40px 0;
  text-align: center;
}

.logo-mark {
  width: 72px;
  height: 72px;
  border-radius: 50%;
  background: var(--color-surface);
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 16px;
  color: var(--color-primary);
  box-shadow: 0 2px 8px var(--color-shadow);
}

.login-logo h2 {
  font-family: var(--font-heading);
  font-size: 22px;
  color: var(--color-text);
  font-weight: 600;
}

.login-form {
  width: 100%;
  padding: 0 16px;
}

.login-form :deep(.van-cell-group) {
  background: var(--color-card);
  border-radius: 12px;
  box-shadow: 0 1px 3px var(--color-shadow);
}

.submit-btn {
  margin: 24px 16px;
}

.test-user-btn {
  margin: 0 16px;
}

.register-link {
  text-align: center;
  margin-top: 24px;
  color: var(--color-text-lighter);
  font-size: 14px;
}

.register-link span {
  color: var(--color-primary);
}
</style>
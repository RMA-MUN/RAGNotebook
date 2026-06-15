<template>
  <div class="my-container">
    <van-nav-bar :title="$t('my.title')" />
    <div class="user-info" @click="goToProfile" v-if="isLogin">
      <div class="avatar">
        <div v-if="userInfo && userInfo.avatar" class="avatar-img">
          <van-image round width="72" height="72" :src="`http://localhost:8000${userInfo.avatar}`" />
        </div>
        <div v-else class="avatar-letter">
          {{ (userInfo?.username || '?')[0].toUpperCase() }}
        </div>
      </div>
      <div class="info">
        <div class="username">{{ userInfo?.username || $t('my.notLoggedIn') }}</div>
        <div class="desc">{{ userBio || $t('profile.bio') }}</div>
      </div>
      <van-icon name="arrow" class="arrow-icon" />
    </div>
    <div class="user-info" v-else>
      <div class="avatar">
        <div class="avatar-letter">?</div>
      </div>
      <div class="info">
        <div class="username">{{ $t('my.notLoggedIn') }}</div>
        <div class="desc">
          <van-button type="primary" size="small" @click="goToLogin" style="margin-right: 10px">{{ $t('my.goToLogin') }}</van-button>
          <van-button size="small" plain @click="goToRegister">{{ $t('my.goToRegister') }}</van-button>
        </div>
      </div>
    </div>

    <div class="menu-list">
      <van-cell-group inset>
        <van-cell :title="$t('my.settings')" is-link @click="goToSettings" />
        <van-cell :title="$t('my.knowledgeBase')" is-link @click="goToKnowledgeBase" />
        <van-cell :title="$t('my.aboutUs')" is-link @click="goToAboutUs" />
        <van-cell v-if="isLogin" :title="$t('my.logout')" @click="handleLogout" />
      </van-cell-group>
    </div>
    <tab-bar />
  </div>
</template>

<script setup>
import { onMounted } from 'vue';
import { useUserStore } from '../store/user';
import { useRouter } from 'vue-router';
import { computed, ref } from 'vue';
import { showDialog, showToast } from 'vant';
import TabBar from '../components/TabBar.vue';
import { useI18n } from 'vue-i18n';

const userStore = useUserStore();
const router = useRouter();
const { t } = useI18n();

// 从store获取用户信息和登录状态
const userInfo = computed(() => userStore.userInfo);
const isLogin = computed(() => userStore.getLoginStatus);
const userBio = computed(() => userStore.getUserBio || t('profile.bio'));

// 跳转到登录页
const goToLogin = () => {
  router.push('/login');
};

// 跳转到注册页
const goToRegister = () => {
  router.push('/register');
};

// 跳转到个人信息页
const goToProfile = () => {
  if (isLogin.value) {
    router.push('/profile');
  }
};



// 跳转到设置页面
const goToSettings = () => {
  router.push('/settings');
};

// 跳转到知识库管理页面
const goToKnowledgeBase = () => {
  router.push('/knowledgebase');
};

// 跳转到关于我们页面
const goToAboutUs = () => {
  router.push('/aboutus');
};

// 退出登录
const handleLogout = () => {
  showDialog({
    title: t('common.confirm'),
    message: t('my.logout') + '?',
    showCancelButton: true,
  }).then((action) => {
    if (action === 'confirm') {
      userStore.logout();
      router.push('/login');
    }
  });
};

// 获取用户信息
onMounted(async () => {
  try {
    await userStore.getUserInfoDetail();
  } catch (error) {
    console.error('获取用户信息失败:', error);
  }
});
</script>

<style scoped>
.my-container {
  padding-top: 46px;
  padding-bottom: 50px;
  background-color: var(--color-bg);
  color: var(--color-text);
  min-height: 100vh;
  box-sizing: border-box;
}

.van-nav-bar {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  z-index: 999;
}

.user-info {
  display: flex;
  align-items: center;
  padding: 20px 16px;
  background: linear-gradient(135deg, var(--color-card) 0%, var(--color-surface) 100%);
  color: var(--color-text);
  border-radius: 12px;
  margin: 16px;
  position: relative;
  box-shadow: 0 1px 4px var(--color-shadow);
}

.arrow-icon {
  position: absolute;
  right: 16px;
  color: var(--color-text-lighter);
}

.avatar {
  margin-right: 16px;
  flex-shrink: 0;
}

.avatar-letter {
  width: 72px;
  height: 72px;
  border-radius: 50%;
  background: var(--color-surface);
  color: var(--color-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 28px;
  font-family: var(--font-heading);
  font-weight: 600;
  border: 2px solid var(--color-border);
}

.info {
  flex: 1;
}

.username {
  font-size: 18px;
  font-weight: 600;
  font-family: var(--font-heading);
  margin-bottom: 4px;
  color: var(--color-text);
}

.desc {
  font-size: 14px;
  color: var(--color-text-lighter);
}

.menu-list {
  margin: 0 16px;
}

.menu-list :deep(.van-cell) {
  border-radius: 8px;
  margin-bottom: 6px;
  background: var(--color-card);
  box-shadow: 0 1px 2px var(--color-shadow);
}
</style>
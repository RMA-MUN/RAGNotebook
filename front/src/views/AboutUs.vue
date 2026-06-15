<template>
  <div class="aboutus-container">
    <van-nav-bar
      :title="$t('settings.aboutUs')"
      left-arrow
      @click-left="onClickLeft"
    />

    <div class="aboutus-content">
      <h2 class="project-title">LangChain RAG FastAPI Service</h2>
      <p class="project-intro">
        基于 LangChain+FastAPI+Django的RAG对话服务，为 LLM 应用提供开箱即用的检索增强能力，适配智能问答、企业知识库等场景。
      </p>

      <van-cell-group inset class="info-group">
        <van-cell class="contact-cell" title="官方GitHub" is-link @click="openLink('https://github.com/RMA-MUN/LangChain-RAG-FastAPI-Service')">
          <template #icon>
            <div class="contact-icon github-icon">
              <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0 0 24 12c0-6.63-5.37-12-12-12z"/></svg>
            </div>
          </template>
          <template #label>
            <span class="link-text">github.com/RMA-MUN/LangChain-RAG-FastAPI-Service</span>
          </template>
        </van-cell>
        <van-cell class="contact-cell" title="作者邮箱" value="n3032747608@163.com">
          <template #icon>
            <div class="contact-icon email-icon">
              <van-icon name="envelop-o" size="14" />
            </div>
          </template>
        </van-cell>
        <van-cell class="contact-cell" title="作者QQ" value="3032747608">
          <template #icon>
            <div class="contact-icon qq-icon">
              <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor">
                <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 3c1.66 0 3 1.34 3 3s-1.34 3-3 3-3-1.34-3-3 1.34-3 3-3zm0 14.2c-2.5 0-4.71-1.28-6-3.22.03-1.99 4-3.08 6-3.08 1.99 0 5.97 1.09 6 3.08-1.29 1.94-3.5 3.22-6 3.22z"/>
              </svg>
            </div>
          </template>
        </van-cell>
      </van-cell-group>

      <h3 class="section-title">技术栈</h3>
      <div class="tech-list">LangChain · FastAPI  · Vue3</div>

      <h3 class="section-title">功能特点</h3>
      <van-cell-group inset>
        <van-cell title="知识库管理" value="支持文档上传、自动语义切片与智能检索" />
        <van-cell title="会话记忆" value="数据库持久化存储，长期会话记忆管理" />
        <van-cell title="高性能异步" value="FastAPI 异步架构，RAG 业务响应更快" />
        <van-cell title="全链路检索" value="BM25+向量混合检索 + CrossEncoder 重排序" />
        <van-cell title="主题定制" value="多种主题颜色，支持个性化切换" />
      </van-cell-group>
    </div>
  </div>
</template>

<script setup>
import { useRouter } from 'vue-router';

const router = useRouter();

const onClickLeft = () => {
  router.back();
};

const openLink = (url) => {
  window.open(url, '_blank');
};
</script>

<style scoped>
.aboutus-container {
  min-height: 100vh;
  background-color: var(--color-bg);
  color: var(--color-text);
  padding-top: 46px;
  padding-bottom: 20px;
}

.aboutus-content {
  padding: 20px 16px;
}

.project-title {
  font-size: 18px;
  font-weight: 600;
  font-family: var(--font-heading);
  margin: 0 0 12px;
  text-align: center;
  color: var(--color-text);
}

.project-intro {
  font-size: 14px;
  color: var(--color-text-light);
  line-height: 1.6;
  margin: 0 0 24px;
  text-align: center;
}

.info-group {
  margin-bottom: 24px;
}

.link-text {
  font-size: 12px;
  color: var(--color-primary);
}

.contact-icon {
  width: 28px;
  height: 28px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-right: 8px;
}

.github-icon {
  background-color: #24292e;
  color: #fff;
}

.email-icon {
  background-color: #7D9B7A;
  color: #fff;
}

.qq-icon {
  background-color: #EBE0D0;
  color: var(--color-text);
}

.contact-cell {
  background: var(--color-card);
  border-radius: 8px;
  margin-bottom: 8px;
  box-shadow: 0 1px 2px var(--color-shadow);
}

.section-title {
  font-size: 14px;
  font-weight: 600;
  font-family: var(--font-heading);
  margin: 0 0 12px;
  color: var(--color-text-lighter);
}

.tech-list {
  font-size: 14px;
  color: var(--color-text);
  margin-bottom: 24px;
  padding: 0 4px;
}
</style>

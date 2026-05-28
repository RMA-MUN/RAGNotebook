# 贡献指南

感谢你对本项目的关注！欢迎任何形式的贡献，包括但不限于：

- 提交 Bug 报告
- 提交功能请求
- 提交代码修复或新功能（Pull Request）
- 改进文档
- 在 Discussions 中回答问题

## 开发环境设置

### 1. 克隆仓库

```bash
git clone https://github.com/RMA-MUN/LangChain-RAG-FastAPI-Service.git
cd LangChain-RAG-FastAPI-Service
```

### 2. 后端设置

```bash
cd backend
uv sync
cp .env.example .env  # 编辑 .env 填入你的配置
```

### 3. 前端设置

```bash
cd front
npm install
# 或 pnpm install
```

### 4. Django 用户服务

```bash
cd DjangoUserService
uv sync
cp .env.example .env
python manage.py migrate
```

## 代码规范

### Python 代码

- 使用 `ruff` 进行代码检查和格式化
- 类型注解优先
- 遵循 PEP 8 命名规范

```bash
cd backend
ruff check .
```

### JavaScript / Vue 代码

- 使用单文件组件（SFC）的 `<script setup>` 语法
- 遵循 Vue 3 Composition API 风格

## 提交 PR 流程

1. Fork 本仓库并创建你的分支：`git checkout -b feature/your-feature`
2. 进行你的变更
3. 确保代码通过 lint 检查
4. 提交前确保本地测试通过
5. 提交 PR 到 `master` 分支

## Commit Message 规范

提交信息应简洁明了，建议使用以下前缀：

- `feat:` 新功能
- `fix:` Bug 修复
- `docs:` 文档更新
- `refactor:` 重构
- `chore:` 构建、依赖等杂项
- `style:` 代码格式调整

## 项目结构速览

```
backend/          # FastAPI 后端
front/            # Vue 3 前端
DjangoUserService/ # Django 用户服务
docs/             # 文档
```

初次贡献者可以关注 `good first issue` 标签的 Issue。

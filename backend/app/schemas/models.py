
from pydantic import BaseModel


class QueryRequest(BaseModel):
    """查询请求模型"""
    session_id: str | None = None
    query: str


class RAGRequest(BaseModel):
    """RAG检索请求模型"""
    query: str


class SessionResponse(BaseModel):
    """会话响应模型"""
    session_id: str
    history: list[tuple[str, str]]


class AgentStep(BaseModel):
    """Agent执行步骤模型"""
    thought: str | None = None
    tool: str | None = None
    tool_input: dict | None = None
    tool_output: str | None = None


class AgentResponse(BaseModel):
    """Agent响应模型"""
    response: str
    session_id: str
    steps: list[AgentStep] | None = None


class RAGResponse(BaseModel):
    """RAG检索响应模型"""
    response: str


class ReorderRequest(BaseModel):
    """重排序请求模型"""
    query: str
    documents: list[str]


class ReorderResponse(BaseModel):
    """重排序响应模型"""
    documents: list[dict]


class KnowledgeDocument(BaseModel):
    """知识库文档信息模型"""
    id: str
    filename: str
    original_filename: str | None = None
    user_id: str | None = None
    chunk_count: int
    preview: str
    created_at: str | None = None


class KnowledgeListResponse(BaseModel):
    """知识库文档列表响应模型"""
    documents: list[KnowledgeDocument]
    total_count: int


class ChunkDetail(BaseModel):
    """
    文档切片详情（含对应图片）。
    images 字段保存该切片所涉及的所有图片URL，前端可据此在切片旁边展示图片。
    """
    chunk_id: str
    index: int
    content: str
    page: int | None = None
    images: list[str] = []


class KnowledgeDocumentDetail(BaseModel):
    """
    知识库文档详情响应模型。
    相比旧版本新增了 chunks（切片级详情，包含每段文本对应的图片）和 images（文档全量图片列表）字段，
    前端可以在文档详情页同时展示文本和图片。
    """
    id: str
    filename: str
    user_id: str | None = None
    chunk_count: int
    content: str
    chunks: list[ChunkDetail] = []
    images: list[str] = []
    created_at: str | None = None


class ChunkInfo(BaseModel):
    """
    文档切片信息模型。
    images 字段保存该切片关联的图片URL，前端在"查看切片"页面中可以按切片展示对应的图片。
    """
    chunk_id: str
    index: int
    content: str
    metadata: dict
    images: list[str] = []


class DocumentChunksResponse(BaseModel):
    """文档切片列表响应模型"""
    filename: str
    total_chunks: int
    chunks: list[ChunkInfo]


class MD5Record(BaseModel):
    """MD5记录模型"""
    md5: str
    filename: str | None = None
    original_filename: str | None = None
    upload_time: str | None = None


class MD5ListResponse(BaseModel):
    """MD5记录列表响应模型"""
    records: list[MD5Record]
    total_count: int


class NoteCreate(BaseModel):
    """创建笔记请求模型"""
    title: str
    content: str
    category: str | None = None
    tags: list[str] | None = None


class NoteUpdate(BaseModel):
    """更新笔记请求模型（所有字段可选）"""
    title: str | None = None
    content: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    is_pinned: bool | None = None


class NoteResponse(BaseModel):
    """笔记响应模型"""
    id: str
    user_id: str
    title: str
    content: str
    tags: list[str] | None = None
    category: str | None = None
    is_pinned: bool = False
    created_at: str | None = None
    updated_at: str | None = None


class NoteListResponse(BaseModel):
    """笔记列表响应模型"""
    notes: list[NoteResponse]
    total_count: int


class NoteSearchRequest(BaseModel):
    """笔记搜索请求模型"""
    query: str


class RelatedNoteItem(BaseModel):
    """关联笔记项模型"""
    id: str
    title: str
    content_preview: str
    similarity: float
    source: str  # 来源：knowledge_base 或 note


class RelatedNotesResponse(BaseModel):
    """关联笔记列表响应模型"""
    notes: list[RelatedNoteItem]


class PageRequest(BaseModel):
    """分页请求模型"""
    page: int = 1
    page_size: int = 20
    category: str | None = None
    tag: str | None = None


class BatchIdsRequest(BaseModel):
    """批量操作请求模型（按 ID 列表）"""
    ids: list[str]


class BatchCategoryRequest(BaseModel):
    """批量更新分类请求模型"""
    ids: list[str]
    category: str


class BatchPinRequest(BaseModel):
    """批量置顶请求模型"""
    ids: list[str]
    is_pinned: bool


class NoteTemplateCreate(BaseModel):
    """创建笔记模板请求模型"""
    name: str
    icon: str = "FileText"
    category: str = ""
    title: str = ""
    content: str = ""
    tags: list[str] = []


class NoteTemplateUpdate(BaseModel):
    """更新笔记模板请求模型"""
    name: str | None = None
    icon: str | None = None
    category: str | None = None
    title: str | None = None
    content: str | None = None
    tags: list[str] | None = None


class NoteTemplateResponse(BaseModel):
    """笔记模板响应模型"""
    id: str
    user_id: str
    name: str
    icon: str
    category: str
    title: str
    content: str
    tags: list[str] | None = None
    is_default: bool = False
    sort_order: int = 0
    created_at: str | None = None
    updated_at: str | None = None


class NoteTemplateReorder(BaseModel):
    """笔记模板重新排序请求模型"""
    ids: list[str]


from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever


class EmptyRetriever(BaseRetriever):
    """始终返回空结果的检索器"""

    async def _aget_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun | None = None,
    ) -> list[Document]:
        return []

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun | None = None,
    ) -> list[Document]:
        return []

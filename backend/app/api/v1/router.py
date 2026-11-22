"""v1 路由聚合入口：各能力模块的 router 在此汇总。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import auth, documents, knowledge, ontology, parse, retrieval, reviews, users

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(documents.router)
api_router.include_router(parse.strategies_router)
api_router.include_router(parse.tasks_router)
api_router.include_router(ontology.router)
api_router.include_router(reviews.flows_router)
api_router.include_router(reviews.reviews_router)
api_router.include_router(knowledge.extraction_router)
api_router.include_router(knowledge.entities_router)
api_router.include_router(knowledge.relations_router)
api_router.include_router(knowledge.graph_router)
api_router.include_router(retrieval.router)

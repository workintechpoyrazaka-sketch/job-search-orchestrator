from contextlib import asynccontextmanager
from fastapi import FastAPI
from src.api.db import open_pool
from src.api.routes_public import router as public_router
from src.api.routes_operator import router as operator_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await open_pool()
    try:
        yield
    finally:
        await app.state.pool.close()


app = FastAPI(lifespan=lifespan)
app.include_router(public_router)
app.include_router(operator_router)

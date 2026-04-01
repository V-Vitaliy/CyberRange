from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI(
    title="AI Security CyberRange API",
    description="Backend for the Vulnerable-by-Design AI CyberRange platform",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health", tags=["System"])
async def health_check():

    return {
        "status": "online",
        "service": "AI CyberRange Core",
        "message": "System initialized. Ready for security analysis queries."
    }
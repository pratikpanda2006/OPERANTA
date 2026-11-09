from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Initialize FastAPI application
app = FastAPI(
    title="MarginGuard API",
    description="Backend API for MarginGuard autonomous retail operations.",
    version="0.1.0",
)

# CORS configuration allowing the React + Vite frontend
origins = [
    "http://localhost:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    """Health check endpoint confirming backend service operational status."""
    return {
        "status": "healthy",
        "service": "marginguard-backend",
        "version": "0.1.0",
        "pilot_merchant": "Northstar Living",
    }


import uvicorn
from app.core.config import settings
from app.main import get_local_ip

if __name__ == "__main__":
    local_ip = get_local_ip()
    port = settings.PORT

    print("=" * 65)
    print(f" {settings.APP_NAME}")
    print("=" * 65)
    print(f" * Localhost access:        http://localhost:{port}")
    print(f" * Local Network (Phone):   http://{local_ip}:{port}")
    print(f" * Interactive API Docs:    http://{local_ip}:{port}/docs")
    print("=" * 65)
    print(" Press Ctrl+C to stop server.\n")

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=port,
        reload=False
    )


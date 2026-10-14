import asyncio
import sys
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.core.config import get_settings
from app.models import File
from app.runtime.registry import RUNTIMES, Language

async def migrate_extensions():
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(select(File))
        files = result.scalars().all()

        print(f"Found {len(files)} files to check.")
        updated_count = 0

        for file in files:
            lang_str = file.language or "python"
            try:
                lang_enum = Language(lang_str)
                runtime_config = RUNTIMES[lang_enum]
                extension = runtime_config.file_extension
            except (ValueError, KeyError):
                print(f"Skipping file {file.id} with unknown language: {lang_str}")
                continue

            if not extension:
                continue

            if not file.path.endswith(extension):
                old_path = file.path
                file.path = f"{file.path}{extension}"
                print(f"Updating file {file.id}: {old_path} -> {file.path}")
                updated_count += 1

        if updated_count > 0:
            await session.commit()
            print(f"Successfully updated {updated_count} files.")
        else:
            print("No files needed updating.")

    await engine.dispose()

if __name__ == "__main__":
    try:
        asyncio.run(migrate_extensions())
    except Exception as e:
        print(f"Migration failed: {e}")
        sys.exit(1)

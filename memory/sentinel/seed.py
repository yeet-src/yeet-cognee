import pathlib

CONTEXT = pathlib.Path(__file__).resolve().parent.parent / "context"


async def seed(reset: bool, dataset: str) -> list[str]:
    import cognee

    if reset:
        await cognee.forget(everything=True)
    documents = sorted(CONTEXT.glob("*.md"))
    for document in documents:
        await cognee.remember(document.read_text(), dataset_name=dataset, self_improvement=False)
    return [d.name for d in documents]

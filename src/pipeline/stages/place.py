from pipeline.stages import Ctx, stage


@stage("place")
def place(ctx: Ctx) -> None:
    raise NotImplementedError("place")

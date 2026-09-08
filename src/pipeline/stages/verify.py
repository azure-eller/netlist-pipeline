from pipeline.stages import Ctx, stage


@stage("verify")
def verify(ctx: Ctx) -> None:
    raise NotImplementedError("verify")

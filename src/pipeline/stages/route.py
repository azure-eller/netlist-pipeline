from pipeline.stages import Ctx, stage


@stage("route")
def route(ctx: Ctx) -> None:
    raise NotImplementedError("route")

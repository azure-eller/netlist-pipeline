from pipeline.stages import Ctx, stage


@stage("constraints")
def constraints(ctx: Ctx) -> None:
    raise NotImplementedError("constraints")

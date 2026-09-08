from pipeline.stages import Ctx, stage


@stage("export")
def export(ctx: Ctx) -> None:
    raise NotImplementedError("export")

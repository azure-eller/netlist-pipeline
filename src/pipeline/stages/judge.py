from pipeline.stages import Ctx, stage


@stage("judge")
def judge(ctx: Ctx) -> None:
    raise NotImplementedError("judge")

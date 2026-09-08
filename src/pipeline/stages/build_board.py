from pipeline.stages import Ctx, stage


@stage("build_board")
def build_board(ctx: Ctx) -> None:
    raise NotImplementedError("build_board")

from pipeline.stages import Ctx, stage


@stage("extract_netlist")
def extract_netlist(ctx: Ctx) -> None:
    raise NotImplementedError("extract_netlist")

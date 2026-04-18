import aws_cdk as cdk
from stacks.specforge_stack import SpecforgeStack

app = cdk.App()
SpecforgeStack(app, "SpecforgeStack")
app.synth()

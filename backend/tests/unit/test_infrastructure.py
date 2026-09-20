"""Static deployment boundaries; these checks never contact AWS."""

import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
STACK = ROOT / "infrastructure" / "stack_request"


class CloudFormationLoader(yaml.SafeLoader):
    pass


def intrinsic(loader, tag, node):
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node)
    else:
        value = loader.construct_mapping(node)
    return {tag: value}


CloudFormationLoader.add_multi_constructor("!", intrinsic)


def template():
    return yaml.load((STACK / "template.yaml").read_text(), Loader=CloudFormationLoader)


def test_all_deploy_environments_acknowledge_named_role():
    data = tomllib.loads((STACK / "samconfig.toml").read_text())
    assert template()["Resources"]["ApiFunctionRole"]["Properties"]["RoleName"]
    for environment in data.values():
        if isinstance(environment, dict) and "deploy" in environment:
            assert (
                "CAPABILITY_NAMED_IAM"
                in environment["deploy"]["parameters"]["capabilities"].split()
            )


def test_cors_preserves_explicit_origins_and_review_put():
    resources = template()["Resources"]
    cors = resources["HttpApi"]["Properties"]["CorsConfiguration"]
    assert {"GET", "POST", "PUT", "OPTIONS"} <= set(cors["AllowMethods"])
    origins = cors["AllowOrigins"]["Split"][1]["Sub"]
    assert origins == "${CorsOrigins},https://${SiteDistribution.DomainName}"
    assert "*" not in origins
    assert (
        resources["ApiFunction"]["Properties"]["Environment"]["Variables"]["CORS_ORIGINS"]["Sub"]
        == origins
    )
    assert "Authorization" in cors["AllowHeaders"] or cors["AllowHeaders"] == ["*"]
    assert "Content-Disposition" in cors["ExposeHeaders"]


def test_site_uses_https_and_private_origin():
    data = template()
    resources = data["Resources"]
    config = resources["SiteDistribution"]["Properties"]["DistributionConfig"]
    assert config["DefaultCacheBehavior"]["ViewerProtocolPolicy"] == "redirect-to-https"
    assert data["Outputs"]["SiteUrl"]["Value"]["Sub"].startswith("https://")
    assert all(resources["SiteBucket"]["Properties"]["PublicAccessBlockConfiguration"].values())
    policy = resources["SiteBucketPolicy"]["Properties"]["PolicyDocument"]["Statement"][0]
    assert policy["Principal"] == {"Service": "cloudfront.amazonaws.com"}
    assert "AWS:SourceArn" in policy["Condition"]["StringEquals"]


def test_deployment_never_reads_or_passes_api_secret_to_vite():
    makefile = (ROOT / "Makefile").read_text()
    deploy = makefile.split("\ndeploy-web:", 1)[1].split("\nput-secret:", 1)[0]
    assert "get-parameter" not in deploy and "API_KEY" not in deploy
    secret_recipe = makefile.split("\nput-secret:", 1)[1].split("\nlogs:", 1)[0]
    assert "\n\t@aws ssm put-parameter" in secret_recipe


def test_request_budget_and_postgres_packaging():
    from sentiment_prep.budget import AWS_CONFIG, REQUEST_SECONDS

    assert REQUEST_SECONDS < template()["Globals"]["Function"]["Timeout"] < 30
    assert AWS_CONFIG.connect_timeout == 1 and AWS_CONFIG.read_timeout == 3
    assert AWS_CONFIG.retries["total_max_attempts"] == 1
    project = tomllib.loads((ROOT / "backend" / "pyproject.toml").read_text())
    assert any(
        dependency.startswith("psycopg[binary]")
        for dependency in project["project"]["dependencies"]
    )
    dockerfile = (ROOT / "backend" / "Dockerfile").read_text()
    assert "lambda/python:3.12" in dockerfile
    assert "import psycopg" in dockerfile and "postgresql+psycopg" in dockerfile

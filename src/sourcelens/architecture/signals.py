import re
from dataclasses import dataclass

from sourcelens.architecture.graph import EdgeType, NodeType

# Dependency-manifest evidence: a package name declared in requirements.txt,
# pyproject.toml or package.json. Weaker evidence than an image name or a
# decorator match, since declaring a dependency doesn't prove it's used.
PACKAGE_SIGNALS: list[tuple[re.Pattern[str], NodeType, str]] = [
    (re.compile(r"\bsqlalchemy\b", re.I), "database", "SQL Database"),
    (re.compile(r"\bpsycopg2?\b|\basyncpg\b", re.I), "database", "PostgreSQL"),
    (re.compile(r"\bpymongo\b|\bmotor\b|\bmongoose\b", re.I), "database", "MongoDB"),
    (re.compile(r"\bmysql\w*\b|\bmariadb\b", re.I), "database", "MySQL"),
    (re.compile(r"\bredis\b|\bioredis\b", re.I), "cache", "Redis"),
    (re.compile(r"\bcelery\b", re.I), "queue", "Celery"),
    (re.compile(r"\bpika\b|\bamqplib\b|\bkombu\b", re.I), "queue", "RabbitMQ"),
    (re.compile(r"\bkafka\b", re.I), "queue", "Kafka"),
    (re.compile(r"\bhttpx\b|\brequests\b|\baxios\b", re.I), "external_api", "HTTP Client"),
    (re.compile(r"\bstripe\b", re.I), "external_api", "Stripe"),
    (re.compile(r"\btwilio\b", re.I), "external_api", "Twilio"),
    (re.compile(r"\bboto3\b|\baws-sdk\b", re.I), "external_api", "AWS"),
]

# docker-compose service image lines: strong evidence, since it names the
# actual infrastructure the repository runs against.
IMAGE_SIGNALS: list[tuple[re.Pattern[str], NodeType, str]] = [
    (re.compile(r"postgres|pgvector", re.I), "database", "PostgreSQL"),
    (re.compile(r"mysql|mariadb", re.I), "database", "MySQL"),
    (re.compile(r"mongo", re.I), "database", "MongoDB"),
    (re.compile(r"redis", re.I), "cache", "Redis"),
    (re.compile(r"rabbitmq", re.I), "queue", "RabbitMQ"),
    (re.compile(r"kafka", re.I), "queue", "Kafka"),
]

DEPENDENCY_MANIFESTS = {"requirements.txt", "pyproject.toml", "package.json", "uv.lock"}


def is_dependency_manifest(path: str) -> bool:
    return path.rsplit("/", 1)[-1] in DEPENDENCY_MANIFESTS


def is_dockerfile(path: str) -> bool:
    return path.rsplit("/", 1)[-1] == "Dockerfile"


def is_compose_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name in {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}


@dataclass(frozen=True)
class RolePattern:
    pattern: re.Pattern[str]
    node_type: NodeType
    confidence: float
    reason: str


# Matched against a symbol's own source text (decorators are included in the
# byte range the parser records), so these catch framework-registered routes/
# tasks regardless of naming convention. Checked before name patterns.
DECORATOR_ROLE_PATTERNS: list[RolePattern] = [
    RolePattern(
        re.compile(
            r"@(?:app|router)\.(?:get|post|put|patch|delete)\(|(?:app|router)\.(?:get|post|put|delete)\("
        ),
        "controller",
        0.9,
        "HTTP route decorator",
    ),
    RolePattern(
        re.compile(r"@(?:celery_app|app|shared)_?task|@celery\.task"),
        "worker",
        0.9,
        "task queue decorator",
    ),
]

# Matched against a symbol's own qualified/simple name when no stronger
# decorator evidence is present.
NAME_ROLE_PATTERNS: list[RolePattern] = [
    RolePattern(re.compile(r"(controller|router)$", re.I), "controller", 0.6, "name pattern"),
    RolePattern(re.compile(r"(service|usecase|manager)$", re.I), "service", 0.6, "name pattern"),
    RolePattern(re.compile(r"(repository|repo|dao)$", re.I), "repository", 0.6, "name pattern"),
    RolePattern(re.compile(r"(worker|consumer)$", re.I), "worker", 0.6, "name pattern"),
    RolePattern(re.compile(r"(auth|jwt|oauth)", re.I), "authentication", 0.6, "name pattern"),
]


@dataclass(frozen=True)
class UsageSignal:
    pattern: re.Pattern[str]
    node_type: NodeType
    label: str
    edge_type: EdgeType
    reason: str


# Matched against a symbol's own source text: what infrastructure a
# controller/service/worker actually talks to, as opposed to what the whole
# repository merely depends on (PACKAGE_SIGNALS).
USAGE_SIGNALS: list[UsageSignal] = [
    UsageSignal(
        re.compile(r"redis\.Redis\(|ioredis|createClient\("),
        "cache",
        "Redis",
        "depends_on",
        "Redis client usage",
    ),
    UsageSignal(
        re.compile(r"\.query\(|session\.execute\(|Session\(|\.objects\.filter\("),
        "database",
        "SQL Database",
        "reads",
        "database query",
    ),
    UsageSignal(
        re.compile(r"\.send_task\(|\.delay\(|\.apply_async\("),
        "queue",
        "Celery",
        "publishes",
        "task enqueued",
    ),
    UsageSignal(
        re.compile(r"httpx\.(get|post|put|delete)\(|requests\.(get|post|put|delete)\(|axios\."),
        "external_api",
        "HTTP Client",
        "depends_on",
        "outbound HTTP call",
    ),
    UsageSignal(
        re.compile(r"boto3\.client\("), "external_api", "AWS", "depends_on", "AWS SDK usage"
    ),
    UsageSignal(
        re.compile(r"stripe\."), "external_api", "Stripe", "depends_on", "Stripe SDK usage"
    ),
]

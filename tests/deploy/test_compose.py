"""
Тесты модуля deploy: статическая проверка deploy/docker-compose.yml и
согласованности .env.example ↔ compose ↔ README «Быстрый старт».

Вход: deploy/docker-compose.yml, .env.example, README.md (корень репо).
Выход: pass/fail pytest, без побочных эффектов (docker не трогаем).
Зависимости: PyYAML (используется только для парсинга YAML-структуры;
переменные ${VAR} компоуза достаём регэкспом из сырого текста, т.к.
PyYAML не делает shell-интерполяцию — это ответственность docker compose).
См. паспорт: docs/dev/modules/deploy.md. Живые проверки docker (healthcheck
статус сервисов, ARM64-манифесты) выполнены руками и зафиксированы в отчёте
module-tester, а не в этих тестах — они зависят от поднятого окружения.
"""

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "deploy" / "docker-compose.yml"
ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"
README_PATH = REPO_ROOT / "README.md"

VAR_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:?[-?][^}]*)?\}")


@pytest.fixture(scope="module")
def compose_text() -> str:
    return COMPOSE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def compose_doc(compose_text: str) -> dict:
    return yaml.safe_load(compose_text)


@pytest.fixture(scope="module")
def env_example_keys() -> set[str]:
    keys: set[str] = set()
    for line in ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped:
            keys.add(stripped.split("=", 1)[0].strip())
    return keys


# ─── test_compose_file_exists_and_parses ────────────────────────────────
# Назначение: compose-файл существует и является валидным YAML с ключом
#   `services`, содержащим ровно три сервиса из паспорта модуля.
# Уровень: ✅
def test_compose_file_exists_and_parses(compose_doc: dict) -> None:
    assert COMPOSE_PATH.exists(), f"compose-файл не найден: {COMPOSE_PATH}"
    assert "services" in compose_doc
    assert set(compose_doc["services"].keys()) == {"neo4j", "vllm", "ollama"}


# ─── test_all_services_have_healthcheck ─────────────────────────────────
# Назначение: каждый из трёх сервисов имеет healthcheck с непустым `test`
#   (требование DEPLOY-001 — пайплайн стартует только после готовности).
# Уровень: ✅
@pytest.mark.parametrize("service", ["neo4j", "vllm", "ollama"])
def test_all_services_have_healthcheck(compose_doc: dict, service: str) -> None:
    svc = compose_doc["services"][service]
    assert "healthcheck" in svc, f"{service}: нет healthcheck (DEPLOY-001)"
    test = svc["healthcheck"].get("test")
    assert test, f"{service}: healthcheck.test пуст"


# ─── test_model_and_graph_data_in_named_volumes ─────────────────────────
# Назначение: данные Neo4j (/data) и веса моделей (vLLM HF-кэш, Ollama)
#   лежат в именованных volume, не в bind mount на хост и не в образе —
#   инвариант модуля (см. docs/dev/modules/deploy.md).
# Уровень: ✅
def test_model_and_graph_data_in_named_volumes(compose_doc: dict) -> None:
    top_level_volumes = set(compose_doc.get("volumes", {}) or {})
    assert top_level_volumes, "нет объявленных top-level volumes"

    expected_named = {
        "neo4j": ["/data"],
        "vllm": ["/root/.cache/huggingface"],
        "ollama": ["/root/.ollama"],
    }
    for service, mount_targets in expected_named.items():
        svc = compose_doc["services"][service]
        mounts = svc.get("volumes", [])
        for target in mount_targets:
            matching = [m for m in mounts if isinstance(m, str) and m.endswith(target)]
            assert matching, f"{service}: не нашли volume-mount на {target}"
            for m in matching:
                source = m.split(":", 1)[0]
                assert source in top_level_volumes, (
                    f"{service}: источник '{source}' для {target} не является "
                    f"именованным top-level volume (bind mount на хост?)"
                )


# ─── test_no_hardcoded_secrets ──────────────────────────────────────────
# Назначение: пароли/токены в compose приходят только из .env (через
#   ${VAR}), в файле нет захардкоженных значений паролей/ключей.
# Уровень: ✅
def test_no_hardcoded_secrets(compose_text: str) -> None:
    # Известные плейсхолдеры/примеры не считаем секретом.
    forbidden_patterns = [
        r"NEO4J_AUTH:\s*[\"']?neo4j/(?!\$\{)",  # захардкоженный пароль после neo4j/
        r"password\s*[:=]\s*[\"'](?!\$\{)[^\"'{}]+[\"']",
    ]
    for pattern in forbidden_patterns:
        match = re.search(pattern, compose_text, re.IGNORECASE)
        assert not match, f"похоже на захардкоженный секрет: {match.group(0) if match else pattern}"


# ─── test_all_compose_vars_in_env_example ───────────────────────────────
# Назначение: каждая переменная ${VAR} (с дефолтом или без), на которую
#   ссылается compose, объявлена (закомментирована допустимо только как
#   секция, но ключ должен присутствовать) в .env.example — иначе
#   пользователь не узнает о ней при заполнении .env.
# Уровень: ✅
def test_all_compose_vars_in_env_example(compose_text: str, env_example_keys: set[str]) -> None:
    referenced_vars = {m.group(1) for m in VAR_REF_RE.finditer(compose_text)}
    missing = sorted(v for v in referenced_vars if v not in env_example_keys)
    assert not missing, (
        f"переменные используются в docker-compose.yml, но отсутствуют "
        f"в .env.example: {missing}"
    )


# ─── test_env_example_has_no_hardcoded_real_secret ──────────────────────
# Назначение: .env.example содержит только плейсхолдеры/дефолты, а не
#   реальный секрет (напр. NEO4J_PASSWORD не должен быть пустым и не
#   должен совпадать с боевым паролем из .env, если тот существует).
# Уровень: ✅
def test_env_example_has_no_hardcoded_real_secret(env_example_keys: set[str]) -> None:
    assert "NEO4J_PASSWORD" in env_example_keys
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        pytest.skip(".env не создан — нечего сравнивать")
    example_password = None
    for line in ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("NEO4J_PASSWORD="):
            example_password = line.split("=", 1)[1].strip()
    real_password = None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("NEO4J_PASSWORD="):
            real_password = line.split("=", 1)[1].strip()
    if real_password:
        assert example_password != real_password, (
            ".env.example содержит реальный пароль из .env — секрет утёк в git"
        )


# ─── test_readme_quick_start_references_existing_services ───────────────
# Назначение: раздел «Быстрый старт» в README ссылается только на
#   существующие в compose сервисы (neo4j/vllm/ollama) в командах
#   `dc exec <service> ...`.
# Уровень: ✅
def test_readme_quick_start_references_existing_services(compose_doc: dict) -> None:
    readme = README_PATH.read_text(encoding="utf-8")
    services = set(compose_doc["services"].keys())
    exec_calls = re.findall(r"dc exec (\S+)", readme)
    assert exec_calls, "в README не найдено ни одной команды `dc exec <service>`"
    unknown = sorted(set(exec_calls) - services)
    assert not unknown, f"README ссылается на несуществующие в compose сервисы: {unknown}"


# ─── test_readme_ports_match_compose_defaults ───────────────────────────
# Назначение: порты, упомянутые в README «Быстрый старт» (через
#   ${..._PORT:-default}), совпадают с дефолтными портами в compose —
#   документация не должна расходиться с реальной конфигурацией.
# Уровень: ✅
def test_readme_ports_match_compose_defaults(compose_text: str) -> None:
    readme = README_PATH.read_text(encoding="utf-8")
    compose_defaults = dict(
        re.findall(r"\$\{(\w+_PORT):-(\d+)\}", compose_text)
    )
    readme_defaults = dict(
        re.findall(r"\$\{(\w+_PORT):-(\d+)\}", readme)
    )
    assert readme_defaults, "в README не найдено ни одного порта с дефолтом ${..._PORT:-N}"
    for var, port in readme_defaults.items():
        assert var in compose_defaults, f"README ссылается на {var}, которого нет в compose"
        assert compose_defaults[var] == port, (
            f"порт {var} в README ({port}) не совпадает с compose ({compose_defaults[var]})"
        )

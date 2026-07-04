#!/usr/bin/env bash
# build_submission_archive.sh — архив кода для пакета сдачи хакатона (A-19).
#
# Назначение: собирает архив исходников репозитория «Ариадна» для облачного
#   диска (пункт 2 пакета сдачи, docs/dev/TASK.md). Использует `git archive
#   HEAD` — берёт файлы ТОЛЬКО из последнего коммита текущей ветки, поэтому
#   в архив автоматически НЕ попадает: `data/` (корпус, 4.9 ГБ, живёт вне git —
#   отдельная ссылка на Яндекс.Диск в README.md), `.venv/` (виртуальное
#   окружение), `logs/` (JSON Lines пайплайна), `artifacts/` (рабочие
#   материалы анализа сессий), любые .env-файлы с секретами — все они либо
#   в .gitignore, либо физически не отслеживаются git и `git archive` их не
#   видит независимо от .gitignore. Незакоммиченные изменения в рабочем
#   дереве тоже не попадут — архив строго = `git show HEAD`.
# Выход: `artifacts/ariadna-submission.tar.gz` (artifacts/ — в .gitignore,
#   архив не будет случайно закоммичен; директория создаётся при отсутствии).
# Инвариант: скрипт ничего не пишет и не удаляет вне artifacts/ — безопасно
#   перезапускать многократно (перезаписывает один и тот же файл).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="${REPO_ROOT}/artifacts"
OUTPUT_FILE="${OUTPUT_DIR}/ariadna-submission.tar.gz"

mkdir -p "${OUTPUT_DIR}"

cd "${REPO_ROOT}"

if ! git rev-parse --git-dir >/dev/null 2>&1; then
    echo "Ошибка: ${REPO_ROOT} не похож на git-репозиторий." >&2
    exit 1
fi

COMMIT="$(git rev-parse --short HEAD)"

echo "Собираю архив кода из коммита ${COMMIT} (git archive HEAD)..."
git archive --format=tar.gz --output="${OUTPUT_FILE}" HEAD

SIZE_HUMAN="$(du -h "${OUTPUT_FILE}" | cut -f1)"
echo "Готово: ${OUTPUT_FILE} (${SIZE_HUMAN}, коммит ${COMMIT})"
echo
echo "Напоминание: data/ (корпус, вне git), .venv/, logs/, artifacts/ и .env"
echo "в архив НЕ входят — корпус раздаётся отдельной ссылкой (см. README.md),"
echo "остальное — локальные/секретные артефакты окружения."

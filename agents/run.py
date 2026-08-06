"""멀티 에이전트 시스템 실행 스크립트

서브에이전트는 .claude/agents/ 디렉토리의 .md 파일로 정의되어 있으며,
팀 리드가 Agent 도구를 통해 이름으로 호출합니다.

사용법:
    python -m agents.run                # 전체 프로세스 (Phase 1~4)
    python -m agents.run --phase 1      # 요구사항 분석만
    python -m agents.run --phase 2      # Phase 1~2 (요구사항 + 계획)
    python -m agents.run --phase 3      # Phase 1~3 (요구사항 + 계획 + 구현)
"""

import argparse
import sys

import anyio
from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage, SystemMessage


# 팀 리드 시스템 프롬프트는 .claude/agents/team-lead.md에 정의되어 있음
# run.py에서는 팀 리드를 직접 실행하는 역할만 담당

TEAM_LEAD_PROMPT_TEMPLATE = """프로젝트를 시작합니다.

{phase_instruction}

먼저 spec.md 파일을 읽어 프로젝트 전체 내용을 파악하고,
docs/02_decision.md의 기존 결정 사항과 CLAUDE.md의 Known Mistakes를 확인한 후,
서브에이전트들을 호출하여 작업을 진행하세요.

서브에이전트는 다음과 같이 Agent 도구로 호출합니다:
- requirements-analyst: 요구사항 분석
- research-planner: 조사 및 계획
- implementer: 코드 구현
- verifier: 테스트 및 검증

병렬 실행이 가능한 에이전트는 단일 메시지에서 동시에 시작하세요.
각 서브에이전트의 산출물을 직접 읽어 검토하고, 품질 게이트를 통과시킨 후 다음 단계로 진행하세요.
"""


async def run_team_lead(phase: int | None = None) -> None:
    """팀 리드 에이전트를 실행하여 프로젝트를 진행한다."""

    if phase == 1:
        phase_instruction = "Phase 1(요구사항 분석)만 진행하세요."
    elif phase == 2:
        phase_instruction = "Phase 1(요구사항 분석)과 Phase 2(조사 및 계획)를 진행하세요."
    elif phase == 3:
        phase_instruction = "Phase 1(요구사항 분석)부터 Phase 3(구현)까지 진행하세요."
    else:
        phase_instruction = "Phase 1부터 Phase 4까지 전체 프로세스를 진행하세요."

    prompt = TEAM_LEAD_PROMPT_TEMPLATE.format(phase_instruction=phase_instruction)

    print("=" * 60)
    print("클라우드 포탈 (vCenter / Hyper-V) - 멀티 에이전트 빌드 시스템")
    print("=" * 60)
    print(f"실행 범위: Phase 1~{phase if phase else 4}")
    print("-" * 60)

    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            cwd=".",
            allowed_tools=["Read", "Write", "Edit", "Glob", "Grep", "Bash", "Agent", "Skill"],
            permission_mode="acceptEdits",
            max_turns=100,
        ),
    ):
        if isinstance(message, ResultMessage):
            print("\n" + "=" * 60)
            print("팀 리드 최종 보고")
            print("=" * 60)
            print(message.result)
        elif isinstance(message, SystemMessage) and message.subtype == "init":
            print(f"세션 ID: {message.data.get('session_id')}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="클라우드 포탈 빌드 시스템"
    )
    parser.add_argument(
        "--phase",
        type=int,
        choices=[1, 2, 3, 4],
        default=None,
        help="실행할 Phase 범위 (1: 요구사항, 2: +계획, 3: +구현, 4: +검증)",
    )
    args = parser.parse_args()

    try:
        anyio.run(run_team_lead, args.phase)
    except KeyboardInterrupt:
        print("\n작업이 중단되었습니다.")
        sys.exit(1)


if __name__ == "__main__":
    main()

from __future__ import annotations

import subprocess
import time

from agentflow_core.config import AgentConfig, ProgramConfig
from agentflow_core.errors import OpencodeError
from agentflow_core.json_stream import extract_session_id, parse_event_stream
from agentflow_core.logger import log_raw_event, log_step, log_verbose
from agentflow_core.session_store import list_sessions


MAX_RETRIES = 5
RETRY_BACKOFF_SECONDS = 3


class OpencodeRunner:
    def __init__(
        self, *, program: ProgramConfig, agents: dict[str, AgentConfig]
    ) -> None:
        self.program = program
        self.agents = agents
        self.sessions: dict[str, str] = {}
        self.session_titles: dict[str, str] = {}

    def _run_once(self, agent: AgentConfig, command: list[str]) -> str | None:
        process = subprocess.Popen(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout_lines: list[str] = []
        assert process.stdout is not None
        for raw_line in process.stdout:
            stdout_lines.append(raw_line)
            stripped = raw_line.rstrip("\n")
            if stripped:
                log_raw_event(stripped)

        stderr_output = ""
        if process.stderr is not None:
            stderr_output = process.stderr.read()
        return_code = process.wait()
        stdout_output = "".join(stdout_lines)

        if return_code != 0:
            raise OpencodeError(
                f"OpenCode run failed for {agent.key}.\n"
                f"stdout:\n{stdout_output}\n"
                f"stderr:\n{stderr_output}"
            )

        events = parse_event_stream(stdout_output)
        return extract_session_id(events)

    def run(self, agent_key: str, prompt: str) -> None:
        agent = self.agents[agent_key]
        before_ids: set[str] = set()
        command = [
            self.program.opencode_bin,
            "run",
            "--attach",
            self.program.attach_url,
            "--model",
            agent.model,
            "--format",
            "json",
        ]
        if agent.variant:
            command.extend(["--variant", agent.variant])

        title: str | None = None
        existing_session = self.sessions.get(agent.key)
        if existing_session:
            command.extend(["--session", existing_session])
            log_step(
                f"Running OpenCode agent={agent.key} role={agent.role_name} model={agent.model} variant={agent.variant or 'default'} with existing session={existing_session}"
            )
        else:
            title = f"{agent.role_name}-{int(time.time() * 1000)}"
            self.session_titles[agent.key] = title
            for session in list_sessions(self.program.opencode_bin):
                session_id = session.get("id")
                if isinstance(session_id, str):
                    before_ids.add(session_id)
            command.extend(["--title", title])
            log_step(
                f"Running OpenCode agent={agent.key} role={agent.role_name} model={agent.model} variant={agent.variant or 'default'} with new session title={title}"
            )
            title = self.session_titles.get(agent.key)

        log_verbose(f"Prompt for agent={agent.key}", prompt)
        command.append(prompt)

        last_error: OpencodeError | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            if attempt > 1:
                log_step(
                    f"Retry {attempt - 1}/{MAX_RETRIES - 1} for agent={agent.key}; waiting {RETRY_BACKOFF_SECONDS}s"
                )
                time.sleep(RETRY_BACKOFF_SECONDS)
            try:
                session_id = self._run_once(agent, command)
            except OpencodeError as exc:
                last_error = exc
                log_step(
                    f"Attempt {attempt}/{MAX_RETRIES} failed for agent={agent.key}: {exc}"
                )
                continue

            if not session_id and title:
                session_id = self.find_session_by_title(title, before_ids)
            if not session_id:
                raise OpencodeError(
                    f"Could not determine session ID for agent {agent.key}"
                )
            self.sessions[agent.key] = session_id
            log_step(f"Completed OpenCode agent={agent.key} session={session_id}")
            return

        raise OpencodeError(
            f"OpenCode failed for agent={agent.key} after {MAX_RETRIES} attempts. Last error: {last_error}"
        ) from last_error

    def find_session_by_title(self, title: str, before_ids: set[str]) -> str | None:
        sessions = list_sessions(self.program.opencode_bin)
        for session in sessions:
            session_id = session.get("id")
            if not isinstance(session_id, str) or session_id in before_ids:
                continue
            if session.get("title") == title:
                return session_id
        for session in sessions:
            if session.get("title") == title and isinstance(session.get("id"), str):
                return session["id"]
        return None

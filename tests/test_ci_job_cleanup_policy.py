import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CiJobCleanupPolicyTests(unittest.TestCase):
    def workflow(self, name: str) -> str:
        return (ROOT / ".forgejo" / "workflows" / name).read_text(encoding="utf-8")

    def test_backend_jobs_expire_promptly_and_kaniko_cleans_up(self):
        text = self.workflow("deploy.yml")
        self.assertEqual(text.count("ttlSecondsAfterFinished: 300"), 4)
        self.assertNotIn("ttlSecondsAfterFinished: 900", text)
        self.assertEqual(text.count("image: gcr.io/kaniko-project/executor:v1.23.2"), 1)
        self.assertEqual(text.count("- --cleanup"), 1)

    def test_frontend_jobs_expire_promptly_and_kaniko_cleans_up(self):
        text = self.workflow("deploy-frontend.yml")
        self.assertEqual(text.count("ttlSecondsAfterFinished: 300"), 2)
        self.assertNotIn("ttlSecondsAfterFinished: 900", text)
        self.assertEqual(text.count("image: gcr.io/kaniko-project/executor:v1.23.2"), 1)
        self.assertEqual(text.count("- --cleanup"), 1)

    def test_architecture_build_waits_are_concurrent(self):
        for workflow in ("deploy.yml", "deploy-frontend.yml"):
            text = self.workflow(workflow)
            self.assertRegex(
                text,
                re.compile(
                    r"wait_job amd64 &\s+AMD64_WAIT_PID=\$!\s+"
                    r"wait_job arm64 &\s+ARM64_WAIT_PID=\$!",
                    re.MULTILINE,
                ),
            )
            self.assertIn('wait "$AMD64_WAIT_PID" || BUILD_STATUS=1', text)
            self.assertIn('wait "$ARM64_WAIT_PID" || BUILD_STATUS=1', text)
            self.assertIn('test "$BUILD_STATUS" -eq 0', text)

    def test_backend_test_job_is_bounded_and_uses_cached_locked_artifacts(self):
        text = self.workflow("deploy.yml")
        self.assertEqual(text.count("activeDeadlineSeconds: 1680"), 1)
        self.assertIn(
            'kubectl wait -n build --for=condition=complete "job/${JOB}" --timeout=1800s',
            text,
        )
        self.assertIn("uv export --quiet --frozen --no-dev", text)
        self.assertIn("uv pip sync --python /workspace/src/.venv/bin/python", text)
        self.assertIn("--require-hashes /tmp/ci-requirements.txt", text)
        self.assertIn("--index-url http://pip-cache.registry.svc.cluster.local/simple", text)
        self.assertNotIn("UV_HTTP_TIMEOUT=300 uv sync --frozen", text)

    def test_migration_check_uses_the_same_cached_lock_contract(self):
        text = self.workflow("migration-check.yml")
        self.assertIn('"$UV_BOOTSTRAP/bin/uv" export --quiet --frozen --no-dev --no-emit-project', text)
        self.assertIn('"$UV_BOOTSTRAP/bin/uv" pip sync', text)
        self.assertIn("--python .venv/bin/python --require-hashes", text)
        self.assertIn("UV_HTTP_RETRIES=10 UV_CONCURRENT_DOWNLOADS=4", text)
        self.assertNotIn(".venv/bin/uv sync --frozen", text)


if __name__ == "__main__":
    unittest.main()

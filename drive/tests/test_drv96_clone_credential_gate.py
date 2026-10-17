"""DRV-96 — the CI clone steps must never put the repo credential on the shared volume.

The deploy workflow cloned with the credential embedded in the URL:

    git clone http://x-token-auth:${GIT_PASSWORD}@host/repo.git /workspace/src

`git clone` persists the credential-bearing remote URL verbatim into
`.git/config`, and `/workspace` is a shared emptyDir the test/kaniko container
mounts — so any code executing there could read the repo token with
`cat .git/config`, regardless of it holding no secret env.

These assertions are the committed negative control: they FAIL when the guard is
weakened (a credential sneaks back into a URL, or `git clone -c`/`git fetch -c`
reintroduces the persisted-config defect). They assert the transport property,
not a manifest-env proxy — a static env assertion was true while the credential
was readable anyway (the PLAT-4720 lesson, shizuha-beta `bedff8d`).
"""
import re
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / ".forgejo" / "workflows" / "deploy.yml"


class CloneCredentialGateTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.text = DEPLOY.read_text()

    def _clone_blocks(self):
        """Yield each git-clone initContainer body for independent assertion."""
        for match in re.finditer(r"- name: git-clone\n(.*?)(?=\n\s*env:)", self.text, re.S):
            yield match.group(1)

    def test_workflow_file_exists(self):
        self.assertTrue(DEPLOY.exists(), "deploy.yml not found — workflow layout changed")

    def test_no_credential_in_any_clone_url(self):
        """A credential in the URL persists to .git/config on the shared volume."""
        blocks = list(self._clone_blocks())
        self.assertGreaterEqual(len(blocks), 2, "expected both clone sites")
        for body in blocks:
            # Matches both the unescaped form (`${GIT_PASSWORD}`) and the
            # YAML-block-scalar escaped form (`\${GIT_PASSWORD}`) the workflow
            # actually contains on disk.
            self.assertNotRegex(
                body,
                r"://[^\s\"']*\\?\$\{?GIT_PASSWORD\}?@",
                msg="credential in the clone URL persists to .git/config, which the "
                    "shared /workspace emptyDir exposes; use `git -c http.extraheader`",
            )

    def test_clone_uses_invocation_scoped_config_not_persisted_config(self):
        """`git -c <k>=<v> clone/fetch` is invocation-scoped. `git clone -c` writes
        the value into the NEW repo's config — same defect by a different route."""
        for body in self._clone_blocks():
            self.assertIn("http.extraheader", body)
            self.assertRegex(
                body, r"git -c http\.extraheader",
                msg="`-c` must precede the subcommand to stay invocation-scoped",
            )
            self.assertNotRegex(
                body, r"git\s+(clone|fetch)\s+(-c|--config)\s",
                msg="`git clone -c` persists the header into .git/config — use "
                    "`git -c ... clone`",
            )

    def test_workspace_is_scrubbed_and_property_checked_at_runtime(self):
        """Static assertions cannot see what lands on a shared volume at runtime,
        so the workflow must scrub git metadata and fail closed before handoff."""
        for body in self._clone_blocks():
            self.assertIn("rm -rf /workspace/src/.git", body,
                          msg="git metadata must not reach the shared volume")
            self.assertIn("grep -rqIF", body,
                          msg="the workflow must assert the no-credential property "
                              "at runtime, not just in the manifest")
            self.assertIn("refusing handoff", body,
                          msg="the runtime residue check must fail closed")

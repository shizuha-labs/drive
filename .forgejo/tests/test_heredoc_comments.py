"""Unquoted manifest heredocs must preserve comment text without shell execution."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest


class HeredocCommentTests(unittest.TestCase):
    def test_manifest_comments_are_literal(self):
        source = (Path(__file__).parents[1] / 'workflows/deploy.yml').read_text()
        blocks = re.findall(r'cat <<YAML[^\n]*\n(.*?)^          YAML$', source, re.M | re.S)
        self.assertTrue(blocks)
        comments = '\n'.join(line for block in blocks for line in block.splitlines()
                             if line.lstrip().startswith('#') and '`' in line)
        self.assertTrue(comments)
        with tempfile.TemporaryDirectory() as directory:
            for command in ('git', 'uv'):
                stub = Path(directory) / command
                stub.write_text('#!/bin/sh\necho unexpected-command >&2\nexit 99\n')
                stub.chmod(0o755)
            result = subprocess.run(['bash'], input='cat <<YAML\n'+comments+'\nYAML\n',
                                    text=True, capture_output=True, cwd=directory,
                                    env=dict(os.environ, PATH=directory+os.pathsep+os.environ['PATH']))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, '')
        self.assertEqual(result.stdout, comments.replace('\\`', '`')+'\n')


if __name__ == '__main__': unittest.main()

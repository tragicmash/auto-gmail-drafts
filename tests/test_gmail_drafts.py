import ast
import base64
import email
import email.policy
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "gmail-drafts"
loader = importlib.machinery.SourceFileLoader("gmail_drafts", str(SCRIPT))
spec = importlib.util.spec_from_loader("gmail_drafts", loader)
gd = importlib.util.module_from_spec(spec)
loader.exec_module(gd)

ENV = {"GMAIL_CLIENT_ID": "cid", "GMAIL_CLIENT_SECRET": "csecret", "GMAIL_REFRESH_TOKEN": "rtoken"}


def enc(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


ORIGINAL = {
    "id": "m1", "threadId": "t1", "snippet": "Your invoice &amp; receipt", "labelIds": ["INBOX"],
    "payload": {
        "mimeType": "multipart/mixed",
        "headers": [{"name": "From", "value": "Shop <billing@shop.example>"},
                    {"name": "Subject", "value": "Invoice 42"},
                    {"name": "Date", "value": "Mon, 16 Nov 2026 10:00:00 +0100"},
                    {"name": "Message-ID", "value": "<abc@shop.example>"}],
        "parts": [
            {"mimeType": "multipart/alternative", "parts": [
                {"mimeType": "text/html", "headers": [{"name": "Content-Type", "value": "text/html; charset=utf-8"}],
                 "body": {"data": enc("<p>Hello&nbsp;David</p><script>x()</script>")}}]},
            {"mimeType": "application/pdf", "filename": "invoice-42.pdf",
             "body": {"attachmentId": "att1", "size": 1234}},
        ],
    },
}


class FakeGoogle:
    """Records every request; answers like Gmail would."""

    def __init__(self):
        self.calls = []

    def __call__(self, method, url, body=None, headers=None):
        self.calls.append((method, url, body, headers or {}))
        if url == gd.TOKEN_URL:
            return {"access_token": "access-1"}
        path = url.replace(gd.API, "").split("?")[0]
        if path == "profile":
            return {"emailAddress": "owner@example.com", "messagesTotal": 3}
        if path == "messages":
            return {"messages": [{"id": "m1"}], "nextPageToken": "p2"}
        if path == "messages/m1":
            return ORIGINAL
        if path == "messages/m1/attachments/att1":
            return {"data": enc("%PDF-1.4 fake")}
        if path == "drafts" and method == "POST":
            return {"id": "r-1", "message": {"id": "m9", "threadId": json.loads(body)["message"].get("threadId")}}
        if path == "drafts" and method == "GET":
            return {"drafts": [{"id": "r-1"}]}
        if path == "drafts/r-1" and method == "GET":
            return {"id": "r-1", "message": {"id": "m9", "snippet": "Hi", "payload": {
                "headers": [{"name": "To", "value": "a@b.example"}, {"name": "Subject", "value": "S"}]}}}
        if path == "drafts/r-1" and method == "PUT":
            return {"id": "r-1", "message": {"id": "m10"}}
        raise AssertionError(f"unexpected call {method} {path}")


class Base(unittest.TestCase):
    def setUp(self):
        self.old = {k: os.environ.get(k) for k in ENV}
        os.environ.update(ENV)
        self.google = FakeGoogle()

    def tearDown(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def run_cmd(self, *argv):
        return gd.run(list(argv), transport=self.google)

    def gmail_calls(self):
        return [(m, u.replace(gd.API, "").split("?")[0]) for m, u, _, _ in self.google.calls if u != gd.TOKEN_URL]


class NoSending(Base):
    def test_allowlist_refuses_every_write_but_drafts(self):
        for method, path in [("POST", "drafts/send"), ("POST", "messages/send"), ("POST", "messages/m1/trash"),
                             ("DELETE", "messages/m1"), ("DELETE", "drafts/r-1"), ("POST", "messages/m1/modify"),
                             ("POST", "messages/batchDelete"), ("POST", "messages/import"),
                             ("POST", "messages"), ("PUT", "drafts/send"), ("GET", "drafts/send"), ("GET", "messages/m1/../send"),
                             ("POST", "settings/filters"), ("PUT", "settings/vacation")]:
            self.assertFalse(gd.allowed(method, path), (method, path))
            with self.assertRaises(gd.Refused):
                gd.Gmail("a", "b", "c", self.google).call(method, path)
        self.assertEqual(self.google.calls, [], "a refused call must not even fetch a token")

    def test_parser_has_no_send_or_delete_command(self):
        commands = set(gd.parser()._subparsers._group_actions[0].choices)
        self.assertEqual(commands, {"profile", "search", "read", "thread", "attachment", "drafts",
                                    "draft-create", "draft-update", "auth"})

    def test_code_never_names_a_send_or_delete_endpoint(self):
        tree = ast.parse(SCRIPT.read_text())
        docstring = ast.get_docstring(tree, clean=False)
        strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                   and n.value != docstring]
        for word in ("/send", "trash", "batchDelete", "modify", "DELETE", "import", "insert"):
            self.assertFalse([s for s in strings if word in s], word)

    def test_ids_cannot_smuggle_a_path(self):
        with self.assertRaises(gd.ToolError):
            self.run_cmd("read", "m1/../../drafts/send")
        with self.assertRaises(gd.ToolError):
            self.run_cmd("draft-update", "r-1/send", "--body", "x")


class Reading(Base):
    def test_profile(self):
        self.assertEqual(self.run_cmd("profile"), {"email": "owner@example.com", "messages": 3, "canSend": False})
        token_call = self.google.calls[0]
        self.assertIn(b"grant_type=refresh_token", token_call[2])
        self.assertEqual(self.google.calls[1][3]["Authorization"], "Bearer access-1")

    def test_search(self):
        out = self.run_cmd("search", "from:shop invoice", "--max", "500")
        self.assertEqual(out["nextPage"], "p2")
        self.assertEqual(out["messages"][0]["subject"], "Invoice 42")
        self.assertEqual(out["messages"][0]["snippet"], "Your invoice & receipt")
        listing = self.google.calls[1][1]
        self.assertIn("maxResults=50", listing)   # capped

    def test_read_falls_back_to_html_text(self):
        out = self.run_cmd("read", "m1")
        self.assertEqual(out["text"], "Hello David")
        self.assertEqual(out["attachments"], [{"name": "invoice-42.pdf", "mimeType": "application/pdf",
                                               "size": 1234, "attachmentId": "att1"}])
        self.assertFalse(out["truncated"])

    def test_attachment_never_replaces_a_file(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "inv.pdf")
            self.assertEqual(self.run_cmd("attachment", "m1", "att1", "--out", out)["bytes"], 13)
            with self.assertRaises(FileExistsError):
                self.run_cmd("attachment", "m1", "att1", "--out", out)


class Drafts(Base):
    def posted_message(self):
        method, url, body, _ = [c for c in self.google.calls if c[0] == "POST" and c[1].endswith("drafts")][-1]
        payload = json.loads(body)["message"]
        raw = base64.urlsafe_b64decode(payload["raw"])
        return payload, email.message_from_bytes(raw, policy=email.policy.default)

    def test_reply_draft_threads_and_quotes_headers(self):
        out = self.run_cmd("draft-create", "--reply-to", "m1", "--body", "Thanks, received.")
        self.assertEqual(out["draftId"], "r-1")
        self.assertIn("Nothing was sent", out["note"])
        payload, msg = self.posted_message()
        self.assertEqual(payload["threadId"], "t1")
        self.assertEqual(msg["To"], "Shop <billing@shop.example>")
        self.assertEqual(msg["Subject"], "Re: Invoice 42")
        self.assertEqual(msg["In-Reply-To"], "<abc@shop.example>")
        self.assertEqual(msg.get_content().strip(), "Thanks, received.")

    def test_new_draft_with_attachment(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "notes.pdf")
            with open(path, "wb") as f:
                f.write(b"%PDF-1.4 x")
            self.run_cmd("draft-create", "--to", "a@b.example", "--subject", "Hello", "--body", "Hi", "--attach", path)
        payload, msg = self.posted_message()
        self.assertNotIn("threadId", payload)
        names = [p.get_filename() for p in msg.iter_attachments()]
        self.assertEqual(names, ["notes.pdf"])

    def test_draft_needs_a_recipient(self):
        with self.assertRaises(gd.ToolError):
            self.run_cmd("draft-create", "--body", "Hi")

    def test_update_and_list(self):
        self.assertEqual(self.run_cmd("draft-update", "r-1", "--to", "a@b.example", "--body", "v2")["messageId"], "m10")
        self.assertEqual(self.run_cmd("drafts")["drafts"][0]["subject"], "S")
        self.assertEqual({m for m, _ in self.gmail_calls()}, {"GET", "PUT"})


class Credentials(Base):
    def test_missing_variable(self):
        os.environ.pop("GMAIL_REFRESH_TOKEN")
        with self.assertRaises(gd.ToolError):
            self.run_cmd("profile")

    def test_output_never_contains_secrets(self):
        outputs = [self.run_cmd("profile"), self.run_cmd("read", "m1"), self.run_cmd("drafts")]
        text = json.dumps(outputs)
        for secret in ("csecret", "rtoken", "access-1"):
            self.assertNotIn(secret, text)


if __name__ == "__main__":
    unittest.main()

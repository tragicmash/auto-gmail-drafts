# gmail-drafts

A command-line tool that reads Gmail and writes drafts. It cannot send, delete, trash, archive or label mail. It is built to be connected to [n8n Nod](https://nod.n8n.io) as a CLI connection, so an agent can prepare replies that a person sends by hand.

It is one Python file with no dependencies beyond the standard library (Python 3.9 or newer).

## What it can and cannot do

| Command | What it does |
| --- | --- |
| `profile` | Prints the account's address. Nod uses it as the connection test. |
| `search QUERY [--max N] [--page TOKEN]` | Lists messages matching a Gmail search, up to 50 per page. |
| `read ID` | Prints one message: headers, plain text and its attachment list. |
| `thread ID` | Prints every message in a thread. |
| `attachment MSG_ID ATT_ID --out PATH` | Saves one attachment to a new file. It never replaces an existing file. |
| `drafts [--max N]` | Lists existing drafts. |
| `draft-create` | Creates a draft. `--reply-to ID` threads it under that message and fills in the recipient and subject. |
| `draft-update DRAFT_ID` | Replaces a draft's content. |
| `auth` | One-time sign-in on your own computer. It copies the refresh token to the clipboard. |

Every Gmail API call goes through one function that refuses any method and path outside a fixed allowlist: reads, and creating or updating drafts. The send calls (`drafts/send`, `messages/send`), deletion, trash and label changes are not in the allowlist, and no command reaches them. The tests check the allowlist, the command list and every string in the code.

The limit sits in this tool, not at Google. Google has no OAuth scope for "drafts but not send": `gmail.compose` covers both. The refresh token could send mail if someone took it out of Nod's vault and used it elsewhere. Nod keeps it encrypted and never shows it to the model.

## Set up a Google Workspace account

Do these steps signed in as the Workspace account the tool will read.

1. In the [Google Cloud console](https://console.cloud.google.com), create a project, for example `gmail-drafts`.
2. Under APIs & Services, Library, enable the **Gmail API**.
3. Under Google Auth Platform, set the audience to **Internal**. An internal app needs no review by Google, and its refresh tokens do not expire after 7 days.
4. Under Data access, add the scopes `gmail.readonly` and `gmail.compose`.
5. Under Clients, create a client of type **Desktop app**. Keep its client ID and secret for the next step.

A personal `@gmail.com` account can only use an external app. Google then shows an "unverified app" warning, and a project in Testing status gets refresh tokens that expire after 7 days.

## Sign in once

On your own Mac, from this folder:

```sh
./gmail-drafts auth
```

The tool asks for the client ID and the hidden client secret, opens Google's sign-in, and copies the refresh token to the clipboard. It prints the granted scopes and never prints the token.

## Connect it to Nod

Ask Nod in chat to connect a command-line tool with these details:

- Command: `gmail-drafts`
- Source: the raw file at a fixed commit, `https://raw.githubusercontent.com/tragicmash/auto-gmail-drafts/<commit>/gmail-drafts`, with its `sha256`
- Environment variables: `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`
- Test command: `profile`

Nod sends a link to a private setup page. Check the source and the fingerprint there, then paste the three values. Nod installs the file as its own Linux user, refuses a download whose `sha256` differs, and passes the values only to this tool.

Get the fingerprint of a commit's file with:

```sh
curl -sL https://raw.githubusercontent.com/tragicmash/auto-gmail-drafts/<commit>/gmail-drafts | shasum -a 256
```

A new version needs a new commit and fingerprint in Nod's connection.

## Tests

```sh
python3 -m unittest discover -s tests
```

The tests run against a fake Gmail API and need no account.

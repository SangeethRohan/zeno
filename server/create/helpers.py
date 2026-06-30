import time

from docker.errors import APIError

from .constants import VALID_LANGUAGES


def build_sql_create_table(table, dialect):
    cols = table.get("columns") or []
    if not cols:
        cols = [{"name": "id", "type": "INTEGER"}]
    col_sql = ", ".join(f'{c["name"]} {c["type"]}' for c in cols)
    return f'CREATE TABLE IF NOT EXISTS {table["name"]} ({col_sql});'


def wait_and_exec(container, attempts, delay, cmd, env=None):
    """Retry an exec_run until it succeeds (exit_code 0) or attempts run out."""
    last_output = b""
    for _ in range(attempts):
        try:
            result = container.exec_run(cmd, environment=env)
            if result.exit_code == 0:
                return True, result.output.decode("utf-8", errors="replace")
            last_output = result.output
        except APIError as e:
            last_output = str(e).encode()
        time.sleep(delay)
    if isinstance(last_output, bytes):
        return False, last_output.decode("utf-8", errors="replace")
    return False, str(last_output)


def create_tables_or_collections(container, engine, username, password, db_name, tables):
    if not tables:
        return []
    results = []
    for table in tables:
        tname = table["name"]
        if engine == "postgres":
            sql = build_sql_create_table(table, "postgres")
            ok, out = wait_and_exec(
                container,
                1,
                0,
                ["psql", "-U", username, "-d", db_name, "-c", sql],
                env={"PGPASSWORD": password},
            )
        elif engine == "mysql":
            sql = build_sql_create_table(table, "mysql")
            ok, out = wait_and_exec(
                container,
                1,
                0,
                ["mysql", "-u", "root", f"-p{password}", db_name, "-e", sql],
            )
        elif engine == "mongo":
            ok, out = wait_and_exec(
                container,
                1,
                0,
                [
                    "mongosh",
                    "--quiet",
                    db_name,
                    "-u",
                    username,
                    "-p",
                    password,
                    "--authenticationDatabase",
                    "admin",
                    "--eval",
                    f"db.createCollection('{tname}')",
                ],
            )
        else:
            ok, out = False, "Tables are not applicable for this engine."
        results.append({"table": tname, "ok": ok, "detail": out.strip()[:500]})
    return results


def wait_until_ready(container, engine, username, password, db_name):
    if engine == "postgres":
        ok, _ = wait_and_exec(container, 25, 1, ["pg_isready", "-U", username])
    elif engine == "mysql":
        ok, _ = wait_and_exec(
            container,
            40,
            1.5,
            ["mysqladmin", "ping", "-h", "127.0.0.1", "-u", "root", f"-p{password}"],
        )
    elif engine == "mongo":
        ok, _ = wait_and_exec(
            container,
            30,
            1,
            [
                "mongosh",
                "--quiet",
                "--eval",
                "db.runCommand({ping:1})",
                "-u",
                username,
                "-p",
                password,
                "--authenticationDatabase",
                "admin",
            ],
        )
    elif engine == "redis":
        ok, _ = wait_and_exec(container, 15, 1, ["redis-cli", "-a", password, "ping"])
    else:
        ok = True
    return ok


def ubuntu_setup_script(languages):
    """Build shell script to install optional dev languages and sample files."""
    packages = ["curl", "wget", "git", "vim", "nano", "build-essential"]
    samples = []

    if "python" in languages:
        packages.extend(["python3", "python3-pip", "python3-venv"])
        samples.append(
            'cat > /workspace/samples/hello.py << \'EOF\'\n'
            'print("Hello from Python")\nEOF'
        )
    if "java" in languages:
        packages.append("default-jdk")
        samples.append(
            'cat > /workspace/samples/Hello.java << \'EOF\'\n'
            'public class Hello {\n'
            '  public static void main(String[] args) {\n'
            '    System.out.println("Hello from Java");\n'
            '  }\n'
            '}\nEOF'
        )
    if "c" in languages:
        packages.append("gcc")
        samples.append(
            'cat > /workspace/samples/hello.c << \'EOF\'\n'
            '#include <stdio.h>\n'
            'int main() {\n'
            '  printf("Hello from C\\n");\n'
            '  return 0;\n'
            '}\nEOF'
        )
    if "cpp" in languages:
        packages.append("g++")
        samples.append(
            'cat > /workspace/samples/hello.cpp << \'EOF\'\n'
            '#include <iostream>\n'
            'int main() {\n'
            '  std::cout << "Hello from C++" << std::endl;\n'
            '  return 0;\n'
            '}\nEOF'
        )
    if "go" in languages:
        packages.append("golang-go")
        samples.append(
            'mkdir -p /workspace/samples/go && '
            'cat > /workspace/samples/go/main.go << \'EOF\'\n'
            'package main\n'
            'import "fmt"\n'
            'func main() { fmt.Println("Hello from Go") }\nEOF'
        )
    if "node" in languages:
        packages.extend(["nodejs", "npm"])
        samples.append(
            'cat > /workspace/samples/hello.js << \'EOF\'\n'
            'console.log("Hello from Node.js");\nEOF'
        )
    if "rust" in languages:
        samples.append(
            'curl --proto "=https" --tlsv1.2 -sSf https://sh.rustup.rs | '
            'sh -s -- -y && . $HOME/.cargo/env'
        )
        samples.append(
            'cat > /workspace/samples/hello.rs << \'EOF\'\n'
            'fn main() { println!("Hello from Rust"); }\nEOF'
        )

    pkg_line = " ".join(sorted(set(packages)))
    sample_cmds = "\n".join(samples) if samples else "true"
    return f"""#!/bin/bash
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq {pkg_line}
mkdir -p /workspace/samples
{sample_cmds}
touch /workspace/.ready
"""


def filter_languages(raw_langs):
    return [lang for lang in raw_langs if lang in VALID_LANGUAGES]

import sqlite3
import sys

c = sqlite3.connect(sys.argv[1])
for r in c.execute("SELECT node_key,required_capabilities_json,allowed_tools_json FROM task_nodes"):
    print(r)

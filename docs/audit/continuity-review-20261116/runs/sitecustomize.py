"""Apply audit network denial to Python child processes as well."""
import sys
from offline_audit_runner import guard

sys.dont_write_bytecode = True
sys.addaudithook(guard)

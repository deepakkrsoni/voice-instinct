#!/bin/sh
echo '{"type":"system","subtype":"hook_started","hook_name":"x"}'
echo 'not json at all'
echo '{"type":"result","subtype":"success","is_error":false,"result":"Four","session_id":"sess-1","permission_denials":[]}'

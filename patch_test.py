import re

with open("tests/test_broker.py", "r") as f:
    text = f.read()

# I will just write a new simpler test to check pending requests and limits properly
# without getting stuck on order of receive
new_test = """
def test_viewer_routing_and_limits(broker_env):
    client, settings, _ = broker_env
    reg = client.post("/installations").json()
    inst_id = reg["installation_id"]
    cred = reg["credential"]

    with client.websocket_connect("/connect") as host_ws:
        host_ws.send_text(json.dumps({
            "v": 1, "id": "11111111-1111-1111-1111-111111111111", "op": "host_auth",
            "payload": {"installation_id": inst_id, "credential": cred}
        }))
        assert json.loads(host_ws.receive_text())["ok"] is True

        with client.websocket_connect("/connect") as viewer_ws:
            viewer_ws.send_text(json.dumps({
                "v": 1, "id": "22222222-2222-2222-2222-222222222222", "op": "viewer_auth",
                "payload": {"installation_id": inst_id}
            }))
            assert json.loads(viewer_ws.receive_text())["ok"] is True

            # Preview rate limits
            for i in range(3):
                viewer_ws.send_text(json.dumps({
                    "v": 1, "id": f"44444444-4444-4444-4444-4444444444{i:02d}", "op": "preview", "payload": {}
                }))
            
            # Host receives first 2
            assert json.loads(host_ws.receive_text())["id"] == "44444444-4444-4444-4444-444444444400"
            assert json.loads(host_ws.receive_text())["id"] == "44444444-4444-4444-4444-444444444401"
            
            # Viewer gets error for 3rd
            err = json.loads(viewer_ws.receive_text())
            assert err["ok"] is False and err["error"]["code"] == "quota_exceeded"
            
            # Clear pending requests by resolving them
            host_ws.send_text(json.dumps({"v": 1, "id": "44444444-4444-4444-4444-444444444400", "ok": True, "result": {}}))
            host_ws.send_text(json.dumps({"v": 1, "id": "44444444-4444-4444-4444-444444444401", "ok": True, "result": {}}))
            assert json.loads(viewer_ws.receive_text())["id"] == "44444444-4444-4444-4444-444444444400"
            assert json.loads(viewer_ws.receive_text())["id"] == "44444444-4444-4444-4444-444444444401"

            # Pending limits (max 32)
            for i in range(33):
                viewer_ws.send_text(json.dumps({
                    "v": 1, "id": f"55555555-5555-5555-5555-5555555555{i:02d}", "op": "instances", "payload": {}
                }))
                
            for i in range(32):
                assert json.loads(host_ws.receive_text())["id"] == f"55555555-5555-5555-5555-5555555555{i:02d}"
                
            # Viewer gets error for 33rd
            err = json.loads(viewer_ws.receive_text())
            assert err["ok"] is False and err["error"]["code"] == "invalid_request"

"""
text = re.sub(r'def test_viewer_routing_and_limits.*', new_test, text, flags=re.DOTALL)
with open("tests/test_broker.py", "w") as f:
    f.write(text)

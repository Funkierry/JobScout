const assert = require("node:assert/strict");
const { resolveDeploymentConfig, authPresentation } = require("./app.js");

assert.deepEqual(resolveDeploymentConfig({}, { hostname: "localhost", port: "5500", protocol: "http:" }), {
  gatewayBase: "http://localhost:8001", capabilityCenterUrl: "http://localhost:3000/workspace/capabilities",
});
assert.equal(resolveDeploymentConfig({}, { hostname: "jobs.example.com", port: "", protocol: "https:" }).gatewayBase, "");
assert.equal(resolveDeploymentConfig({}, { hostname: "127.0.0.1", port: "2026", protocol: "http:" }).gatewayBase, "");
assert.equal(resolveDeploymentConfig({ gatewayBase: "" }, { hostname: "localhost", port: "5500", protocol: "http:" }).gatewayBase, "");
assert.equal(resolveDeploymentConfig({ gatewayBase: "https://api.example.com/" }).gatewayBase, "https://api.example.com");
assert.throws(() => resolveDeploymentConfig({ gatewayBase: "javascript:alert(1)" }));
assert.throws(() => resolveDeploymentConfig({ gatewayBase: "https://secret:password@api.example.com" }));
assert.throws(() => resolveDeploymentConfig({ capabilityCenterUrl: "//untrusted.example/path" }));
assert.equal(authPresentation({ needs_setup: true, registration_enabled: false }).mode, "initialize");
assert.equal(authPresentation({ needs_setup: false, registration_enabled: false }).allowRegistration, false);
assert.equal(authPresentation({ needs_setup: false, registration_enabled: true }).allowRegistration, true);
console.log("PASS: same-origin/local endpoints and first-run authentication policy");

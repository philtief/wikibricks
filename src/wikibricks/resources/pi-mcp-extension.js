"use strict";

const fs = require("node:fs");
const readline = require("node:readline");
const { spawn } = require("node:child_process");

const configPath = __WIKIBRICKS_PI_CONFIG_PATH__;

module.exports = function registerWikiBricks(pi) {
  const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
  let child = null;
  let ready = null;
  let nextId = 1;
  const pending = new Map();

  function rejectPending(error) {
    for (const { reject } of pending.values()) reject(error);
    pending.clear();
  }

  function send(message) {
    if (!child || !child.stdin.writable) throw new Error("WikiBricks MCP server is not running");
    child.stdin.write(`${JSON.stringify(message)}\n`);
  }

  function request(method, params) {
    const id = nextId++;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      try {
        send({ jsonrpc: "2.0", id, method, params });
      } catch (error) {
        pending.delete(id);
        reject(error);
      }
    });
  }

  function start() {
    if (ready) return ready;

    const process = spawn(config.mcpCommand, [], { stdio: ["pipe", "pipe", "pipe"] });
    child = process;
    process.stderr.resume();
    const lines = readline.createInterface({ input: process.stdout });
    lines.on("line", (line) => {
      let message;
      try {
        message = JSON.parse(line);
      } catch (error) {
        rejectPending(new Error(`Invalid response from WikiBricks MCP server: ${error.message}`));
        return;
      }
      if (!Object.prototype.hasOwnProperty.call(message, "id")) return;
      const waiting = pending.get(message.id);
      if (!waiting) return;
      pending.delete(message.id);
      if (message.error) waiting.reject(new Error(message.error.message || "WikiBricks MCP error"));
      else waiting.resolve(message.result);
    });
    process.on("error", rejectPending);
    process.on("exit", (code, signal) => {
      lines.close();
      if (child === process) {
        child = null;
        ready = null;
      }
      rejectPending(new Error(`WikiBricks MCP server exited (${signal || code})`));
    });

    ready = request("initialize", {
      protocolVersion: "2025-06-18",
      capabilities: {},
      clientInfo: { name: "wikibricks-pi", version: "1" },
    }).then(() => {
      send({ jsonrpc: "2.0", method: "notifications/initialized" });
    });
    return ready;
  }

  for (const schema of config.tools) {
    pi.registerTool({
      name: schema.name,
      label: schema.name,
      description: schema.description,
      parameters: schema.inputSchema,
      execute: async (_toolCallId, arguments_) => {
        await start();
        return request("tools/call", { name: schema.name, arguments: arguments_ });
      },
    });
  }

  pi.on("session_shutdown", async () => {
    if (!child) return;
    const process = child;
    child = null;
    ready = null;
    rejectPending(new Error("Pi session ended"));
    process.kill();
  });
};

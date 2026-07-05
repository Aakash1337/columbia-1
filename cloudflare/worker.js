/* Cloudflare Worker front door for the Columbia-1 container.
 *
 * The Worker itself does almost nothing — Columbia-1 is the FastAPI app inside
 * the container (built from ../Dockerfile). This shim's whole job is to route
 * every request to a single long-lived container instance and to hand the
 * app its secrets as environment variables.
 *
 * Secrets are set once with wrangler and passed through at container start:
 *   npx wrangler secret put COLUMBIA_ACCESS_CODE
 *   npx wrangler secret put GEMINI_API_KEY
 *
 * Lifecycle: the container boots on the first request (a few seconds of cold
 * start) and sleeps after `sleepAfter` with no traffic. The web UI polls
 * running jobs every ~0.8s, so a job keeps its container awake as long as the
 * page stays open — close the tab mid-dub and a long-idle instance may sleep.
 */

import { Container, getContainer } from "@cloudflare/containers";

export class Columbia1Container extends Container {
  defaultPort = 8080;
  sleepAfter = "20m";

  constructor(ctx, env) {
    super(ctx, env);
    this.envVars = {
      COLUMBIA_MODE: "api",
      COLUMBIA_HOST: "0.0.0.0",
      PORT: "8080",
      // Worker secrets -> container env. Empty string = feature off, same as
      // never setting it (config normalizes blank to null).
      COLUMBIA_ACCESS_CODE: env.COLUMBIA_ACCESS_CODE ?? "",
      GEMINI_API_KEY: env.GEMINI_API_KEY ?? "",
      GEMINI_MODEL: env.GEMINI_MODEL ?? "",
    };
  }
}

export default {
  async fetch(request, env) {
    // One named instance ("main") = one app, one library, one job queue —
    // matching the app's own one-job-at-a-time design.
    return getContainer(env.COLUMBIA_CONTAINER, "main").fetch(request);
  },
};

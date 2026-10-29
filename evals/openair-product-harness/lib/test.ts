/**
 * Product harness test entry.
 *
 * - Auth-required mode: dev JWT API access + seeded AuthContext before first navigation.
 * - Auth-open mode: no login dependency; plain API/page fixtures.
 */
import { test as base, expect, type Page } from "@playwright/test";
import {
  createApiRequestContext,
  loginAsDev,
  resolveHarnessAuthRequired,
  seedBrowserAuth,
  type DevSession,
} from "./auth";

type HarnessFixtures = {
  authRequired: boolean;
  devSession: DevSession | null;
};

export const test = base.extend<HarnessFixtures>({
  authRequired: async ({ playwright }, use) => {
    const required = await resolveHarnessAuthRequired(playwright);
    await use(required);
  },

  devSession: async ({ playwright, authRequired }, use) => {
    if (!authRequired) {
      await use(null);
      return;
    }

    const session = await loginAsDev(playwright);
    try {
      await use(session);
    } finally {
      await session.request.dispose();
    }
  },

  page: async ({ page, authRequired, devSession }, use) => {
    if (authRequired && devSession) {
      await seedBrowserAuth(page, devSession);
    }
    await use(page);
  },

  request: async ({ playwright, authRequired, devSession }, use) => {
    if (authRequired && devSession) {
      await use(devSession.request);
      return;
    }

    const request = await createApiRequestContext(playwright);
    try {
      await use(request);
    } finally {
      await request.dispose();
    }
  },
});

export { expect };
export type { Page };

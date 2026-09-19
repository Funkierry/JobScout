import { describe, expect, test } from "@rstest/core";

import { handOffAuthorizationWindow } from "@/core/integrations/lark/authorization-window";

describe("Lark authorization window handoff", () => {
  test("schedules navigation before detaching the opener", () => {
    const events: string[] = [];
    const popup = {
      location: {
        replace(url: string) {
          events.push(`navigate:${url}`);
        },
      },
      get opener() {
        return {};
      },
      set opener(value: unknown) {
        events.push(`opener:${String(value)}`);
      },
    };

    handOffAuthorizationWindow(popup, "https://accounts.feishu.cn/device");

    expect(events).toEqual([
      "navigate:https://accounts.feishu.cn/device",
      "opener:null",
    ]);
  });
});

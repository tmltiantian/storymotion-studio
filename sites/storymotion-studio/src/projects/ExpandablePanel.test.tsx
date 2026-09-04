import "@testing-library/jest-dom/vitest";

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ExpandablePanel } from "./ExpandablePanel";

describe("ExpandablePanel", () => {
  it("keeps detailed controls closed until the creator asks to view them", async () => {
    const user = userEvent.setup();
    render(<ExpandablePanel title="生成设置" summary="2 个镜头">内容</ExpandablePanel>);

    const trigger = screen.getByRole("button", { name: /生成设置/ });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("内容")).not.toBeInTheDocument();

    await user.click(trigger);

    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("内容")).toBeVisible();
  });
});

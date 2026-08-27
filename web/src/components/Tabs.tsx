import { Tabs as BaseTabs } from "@base-ui/react/tabs";
import type { ReactNode } from "react";

export interface TabItem {
  value: string;
  label: string;
  panel: ReactNode;
}

interface TabsProps {
  items: TabItem[];
  defaultValue?: string;
}

/**
 * "MARKETS  HISTORY  EVIDENCE" style -- a thin underline indicator sliding
 * beneath small caps labels, not a pill-tab bar. Base UI supplies roving
 * keyboard focus and ARIA tab/tabpanel wiring; the indicator's position is
 * driven entirely by Base UI's own --active-tab-left/--active-tab-width
 * CSS variables, exposed on Tabs.Indicator.
 */
export function Tabs({ items, defaultValue }: TabsProps) {
  return (
    <BaseTabs.Root defaultValue={defaultValue ?? items[0]?.value} className="ppi-tabs">
      <BaseTabs.List className="ppi-tabs__list">
        {items.map((item) => (
          <BaseTabs.Tab key={item.value} value={item.value} className="ppi-tabs__tab">
            {item.label}
          </BaseTabs.Tab>
        ))}
        <BaseTabs.Indicator className="ppi-tabs__indicator" />
      </BaseTabs.List>
      {items.map((item) => (
        <BaseTabs.Panel key={item.value} value={item.value} className="ppi-tabs__panel">
          {item.panel}
        </BaseTabs.Panel>
      ))}
    </BaseTabs.Root>
  );
}

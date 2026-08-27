import { Select as BaseSelect } from "@base-ui/react/select";
import { Check, ChevronDown } from "lucide-react";

export interface SelectOption {
  value: string;
  label: string;
}

interface SelectProps {
  label: string;
  value: string;
  onValueChange: (value: string) => void;
  options: SelectOption[];
}

/**
 * A compact research-tool control, not a generic dropdown: Base UI Select
 * provides the interaction behavior (keyboard nav, focus management, ARIA),
 * every visual detail is PPI's own -- sharp corners, hairline border, mono
 * value, editorial popup styling matching the rest of the design system.
 */
export function Select({ label, value, onValueChange, options }: SelectProps) {
  return (
    <label className="ppi-select">
      <span className="ppi-select__label">{label}</span>
      <BaseSelect.Root items={options} value={value} onValueChange={(next) => onValueChange(next ?? "")}>
        <BaseSelect.Trigger className="ppi-select__trigger">
          <BaseSelect.Value />
          <BaseSelect.Icon className="ppi-select__icon">
            <ChevronDown size={14} aria-hidden="true" />
          </BaseSelect.Icon>
        </BaseSelect.Trigger>
        <BaseSelect.Portal>
          <BaseSelect.Positioner className="ppi-select__positioner" sideOffset={4}>
            <BaseSelect.Popup className="ppi-select__popup">
              <BaseSelect.List>
                {options.map((option) => (
                  <BaseSelect.Item key={option.value} value={option.value} className="ppi-select__item">
                    <BaseSelect.ItemText>{option.label}</BaseSelect.ItemText>
                    <BaseSelect.ItemIndicator className="ppi-select__indicator">
                      <Check size={13} aria-hidden="true" />
                    </BaseSelect.ItemIndicator>
                  </BaseSelect.Item>
                ))}
              </BaseSelect.List>
            </BaseSelect.Popup>
          </BaseSelect.Positioner>
        </BaseSelect.Portal>
      </BaseSelect.Root>
    </label>
  );
}

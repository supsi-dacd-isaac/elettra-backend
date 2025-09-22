import type { ReactNode } from 'react';

export default function Panel({ className, children }: { className?: string; children: ReactNode }) {
  const base = "w-full p-3 md:p-4 bg-white border border-neutral-200 rounded-xl shadow-sm";
  return <div className={className ? `${base} ${className}` : base}>{children}</div>;
}



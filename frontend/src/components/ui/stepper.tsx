import { Check } from 'lucide-react';
import { cn } from '@/lib/utils';

interface StepperProps {
  steps: string[];
  currentStep: number;
  className?: string;
}

export function Stepper({ steps, currentStep, className }: StepperProps) {
  return (
    <nav
      aria-label="步骤进度"
      className={cn('w-full', className)}
    >
      <ol className="flex w-full items-center">
        {steps.map((label, index) => {
          const stepNumber = index + 1;
          const isCompleted = stepNumber < currentStep;
          const isCurrent = stepNumber === currentStep;
          const isUpcoming = stepNumber > currentStep;
          const isLast = index === steps.length - 1;

          return (
            <li
              key={label}
              className={cn(
                'flex items-center',
                isLast ? 'flex-none' : 'flex-1',
              )}
              aria-current={isCurrent ? 'step' : undefined}
            >
              <div className="flex flex-col items-center gap-2">
                <div
                  className={cn(
                    'flex size-8 items-center justify-center rounded-full text-sm font-semibold transition-colors duration-200',
                    isCompleted && 'bg-primary text-primary-foreground',
                    isCurrent && 'border-2 border-primary text-primary',
                    isUpcoming && 'border border-muted-foreground/30 text-muted-foreground',
                  )}
                  aria-label={`步骤 ${stepNumber}${isCompleted ? '，已完成' : isCurrent ? '，当前步骤' : '，待完成'}`}
                >
                  {isCompleted ? (
                    <Check className="size-4" aria-hidden="true" />
                  ) : (
                    stepNumber
                  )}
                </div>
                <span
                  className={cn(
                    'hidden text-center text-xs font-medium sm:block',
                    isUpcoming ? 'text-muted-foreground' : 'text-foreground',
                  )}
                >
                  {label}
                </span>
              </div>
              {!isLast && (
                <div
                  className={cn(
                    'mx-3 h-0.5 flex-1 rounded-full transition-colors duration-200',
                    isCompleted ? 'bg-primary' : 'bg-muted-foreground/20',
                  )}
                  aria-hidden="true"
                />
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

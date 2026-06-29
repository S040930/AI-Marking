import { Link, Outlet, useLocation } from 'react-router-dom';
import { Check, History, Settings, Upload } from 'lucide-react';
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
} from '@/components/ui/sidebar';

const navItems = [
  { key: 'upload', to: '/', label: '上传作业', icon: Upload },
  { key: 'history', to: '/history', label: '历史记录', icon: History },
  { key: 'settings', to: '/settings', label: '系统设置', icon: Settings },
] as const;

function getActiveKey(pathname: string): string {
  if (pathname.startsWith('/history')) return 'history';
  if (pathname.startsWith('/settings')) return 'settings';
  return 'upload';
}

export default function MainLayout() {
  const { pathname } = useLocation();
  const activeKey = getActiveKey(pathname);

  return (
    <SidebarProvider className="h-svh">
      <Sidebar className="border-r bg-sidebar">
        <SidebarHeader>
          <div className="flex items-center gap-2.5 px-3 py-4">
            <div className="flex size-9 items-center justify-center rounded-lg bg-muted text-primary">
              <Check className="size-5" />
            </div>
            <div className="flex flex-col">
              <span className="text-[15px] font-bold leading-tight tracking-tight">
                AI 作业批改
              </span>
              <span className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                Smart Grading
              </span>
            </div>
          </div>
        </SidebarHeader>
        <SidebarContent>
          <SidebarGroup>
            <SidebarGroupContent>
              <SidebarMenu className="gap-1 px-2">
                {navItems.map((item) => {
                  const Icon = item.icon;
                  const isActive = activeKey === item.key;
                  return (
                    <SidebarMenuItem key={item.key}>
                      <SidebarMenuButton
                        asChild
                        isActive={isActive}
                        className="group h-10 gap-3 rounded-lg px-3 transition-all"
                      >
                        <Link to={item.to}>
                          <span
                            className={`flex size-7 items-center justify-center rounded-md transition-colors ${
                              isActive
                                ? 'bg-primary/10 text-primary'
                                : 'bg-muted text-muted-foreground group-hover:bg-accent group-hover:text-accent-foreground'
                            }`}
                          >
                            <Icon className="size-4" />
                          </span>
                          <span
                            className={`text-sm font-medium ${
                              isActive ? 'text-foreground' : 'text-muted-foreground'
                            }`}
                          >
                            {item.label}
                          </span>
                        </Link>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  );
                })}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>
      </Sidebar>
      <SidebarInset className="min-h-0">
        <header className="flex h-16 shrink-0 items-center gap-3 border-b bg-background px-6">
          <SidebarTrigger className="text-muted-foreground" />
          <div className="h-6 w-px bg-border" />
          <span className="text-[15px] font-semibold tracking-tight text-foreground">
            {navItems.find((i) => i.key === activeKey)?.label ?? 'AI 作业批改系统'}
          </span>
        </header>
        <div className="flex-1 overflow-auto bg-background p-8">
          <Outlet />
        </div>
      </SidebarInset>
    </SidebarProvider>
  );
}

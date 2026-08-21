import { Suspense } from 'react';
import { Link, Outlet, useLocation } from 'react-router-dom';
import {
  BookOpen,
  Check,
  ChevronsLeft,
  ChevronsRight,
  History,
  Settings,
  Upload,
} from 'lucide-react';
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
  useSidebar,
} from '@/components/ui/sidebar';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { useLanguage } from '@/i18n';

const navItems = [
  { key: 'upload', to: '/questions/upload', label: '上传题目', icon: Upload },
  { key: 'questions', to: '/questions', label: '题目库', icon: BookOpen },
  { key: 'history', to: '/history', label: '历史记录', icon: History },
  { key: 'settings', to: '/settings', label: '系统设置', icon: Settings },
] as const;

function getActiveKey(pathname: string): string | null {
  if (pathname === '/questions/upload') return 'upload';
  if (pathname.startsWith('/questions')) return 'questions';
  if (pathname.startsWith('/history')) return 'history';
  if (pathname.startsWith('/settings')) return 'settings';
  // 首页、批改结果、协同评分等不属于任何侧边栏项，不高亮
  return null;
}

function PageSkeleton() {
  return (
    <div className="space-y-4">
      <Skeleton className="h-8 w-48" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-3/4" />
      <Skeleton className="h-64 w-full" />
    </div>
  );
}

// 桌面端侧边栏底部常驻的收起/展开切换按钮；移动端侧边栏是抽屉，由顶部栏按钮开关。
function SidebarCollapseToggle() {
  const { state, toggleSidebar } = useSidebar();
  const { t } = useLanguage();
  const collapsed = state === 'collapsed';
  const label = t(collapsed ? '展开侧边栏' : '收起侧边栏');

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          onClick={toggleSidebar}
          className="mx-auto size-9 text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
          aria-label={label}
        >
          {collapsed ? <ChevronsRight className="size-4" /> : <ChevronsLeft className="size-4" />}
        </Button>
      </TooltipTrigger>
      <TooltipContent side="right" align="center">
        {label}
      </TooltipContent>
    </Tooltip>
  );
}

export default function MainLayout() {
  const { pathname } = useLocation();
  const activeKey = getActiveKey(pathname);
  const { locale, toggleLocale, t } = useLanguage();

  return (
    <>
      <a
        href="#main-content"
        className="sr-only fixed left-4 top-4 z-[100] rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-lg focus:not-sr-only focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
      >
        {t('跳转到主内容')}
      </a>
      <SidebarProvider className="h-svh">
        <Sidebar collapsible="icon" className="border-r bg-sidebar">
        <SidebarHeader>
          <div className="flex items-center gap-2.5 px-3 py-4 group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0">
            <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted text-primary">
              <Check className="size-5" />
            </div>
            <div className="flex flex-col group-data-[collapsible=icon]:hidden">
              <span className="text-[15px] font-bold leading-tight tracking-tight">
                {t('AI 作业批改')}
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
              <SidebarMenu className="gap-1 px-2 group-data-[collapsible=icon]:px-0">
                {navItems.map((item) => {
                  const Icon = item.icon;
                  const isActive = activeKey === item.key;
                  return (
                    <SidebarMenuItem key={item.key}>
                      <SidebarMenuButton
                        asChild
                        isActive={isActive}
                        tooltip={t(item.label)}
                        className="group h-10 gap-3 rounded-lg px-3 transition-all group-data-[collapsible=icon]:mx-auto group-data-[collapsible=icon]:size-8 group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:rounded-lg group-data-[collapsible=icon]:bg-transparent group-data-[collapsible=icon]:p-0 group-data-[collapsible=icon]:hover:bg-sidebar-accent data-[active=true]:group-data-[collapsible=icon]:bg-transparent"
                      >
                        <Link
                          to={item.to}
                          className="group-data-[collapsible=icon]:grid group-data-[collapsible=icon]:size-8 group-data-[collapsible=icon]:place-items-center group-data-[collapsible=icon]:gap-0"
                        >
                          <span
                            className={`flex size-7 shrink-0 items-center justify-center rounded-md transition-all group-data-[collapsible=icon]:absolute group-data-[collapsible=icon]:inset-0 group-data-[collapsible=icon]:m-auto group-data-[collapsible=icon]:size-8 ${
                              isActive
                                ? 'bg-primary/10 text-primary group-data-[collapsible=icon]:bg-transparent'
                                : 'bg-muted text-muted-foreground group-hover:translate-x-0.5 group-hover:scale-105 group-hover:bg-accent group-hover:text-accent-foreground motion-reduce:group-hover:translate-x-0 group-data-[collapsible=icon]:bg-transparent group-data-[collapsible=icon]:group-hover:bg-transparent'
                            }`}
                          >
                            <Icon className="size-4" />
                          </span>
                          <span
                            className={`text-sm font-medium group-data-[collapsible=icon]:hidden ${
                              isActive ? 'text-foreground' : 'text-muted-foreground'
                            }`}
                          >
                            {t(item.label)}
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
        <SidebarFooter>
          <div className="hidden py-2 md:flex md:justify-center">
            <SidebarCollapseToggle />
          </div>
        </SidebarFooter>
      </Sidebar>
      <SidebarInset className="min-h-0">
        <header className="flex h-16 shrink-0 items-center gap-3 border-b bg-background px-6">
          <SidebarTrigger className="text-muted-foreground md:hidden" />
          <div className="h-6 w-px bg-border" />
            <span className="text-[15px] font-semibold tracking-tight text-foreground">
              {activeKey ? t(navItems.find((i) => i.key === activeKey)?.label ?? 'AI 作业批改系统') : t('AI 作业批改系统')}
            </span>
          <button
            type="button"
            onClick={toggleLocale}
            className="ml-auto rounded-md border px-2.5 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            aria-label={locale === 'zh-CN' ? '切换到英文' : 'Switch to Chinese'}
          >
            {locale === 'zh-CN' ? 'English' : '中文'}
          </button>
        </header>
        <div
          id="main-content"
          className="flex-1 overflow-auto bg-background p-8"
          tabIndex={-1}
        >
          <Suspense fallback={<PageSkeleton />}>
            <Outlet />
          </Suspense>
        </div>
      </SidebarInset>
    </SidebarProvider>
    </>
  );
}

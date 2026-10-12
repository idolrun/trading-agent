"use client"

import { LogOut, Monitor, Moon, Sun, User } from "lucide-react"
import { useTheme } from "next-themes"
import type { ReactNode } from "react"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

/** Account, appearance, notifications (`children`) and sign-out in one menu: the header's secondary controls. */
export function UserMenu({
  email,
  signingOut,
  onSignOut,
  children,
}: {
  email: string
  signingOut: boolean
  onSignOut: () => void
  children?: ReactNode
}) {
  const { setTheme } = useTheme()
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button type="button" variant="ghost" size="icon" className="size-9" aria-label="Account menu">
          <User aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuLabel className="truncate font-normal text-muted-foreground" title={email}>
          {email}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => setTheme("light")}>
          <Sun aria-hidden /> Light theme
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => setTheme("dark")}>
          <Moon aria-hidden /> Dark theme
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => setTheme("system")}>
          <Monitor aria-hidden /> System theme
        </DropdownMenuItem>
        {children ? (
          <>
            <DropdownMenuSeparator />
            <div className="px-2 py-1.5">{children}</div>
          </>
        ) : null}
        <DropdownMenuSeparator />
        <DropdownMenuItem disabled={signingOut} onClick={onSignOut}>
          <LogOut aria-hidden /> Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

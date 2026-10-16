<script lang="ts">
	import { onMount } from "svelte";
	import { link } from "svelte-routing";
	import { HomeIcon, UsersIcon, BookOpenIcon, PlusCircleIcon, EditIcon, FilePlusIcon, CopyIcon, FolderPlusIcon, RefreshCcwIcon, UploadCloudIcon, CheckSquareIcon, SunriseIcon, FlagIcon,StarIcon, BookIcon, PhoneIcon, ActivityIcon, TabletIcon, TargetIcon, TerminalIcon } from "svelte-feather-icons";

	import SavedPages, { addPage } from "../../stores/saved-pages";
	import PageEntry from "../saved-pages/PageEntry.svelte";
	import rankStore from "../../stores/rank";
	let savedPages: { url: string; title: string }[] = [];
	SavedPages.subscribe((v) => (savedPages = v));

	let rankData: any = null;
	rankStore.subscribe((v) => (rankData = v));

	const hasPerm = (permission?: string) => {
		if (!permission) return true;
		if (rankData && rankData.rank && Array.isArray(rankData.rank.permissions)) {
			return rankData.rank.permissions.includes(permission);
		}
		return false;
	};

	const navItems = [
		{
			name: "Dashboard",
			link: "/admin/",
			icon: HomeIcon,
		},
		{
			name: "Players",
			link: "/admin/players",
			icon: UsersIcon,
			permission: "GetUsersList",
		},
		{
			name: 'Groups',
			link: '/admin/groups',
			icon: ActivityIcon,
			permission: 'GetGroupManageInfo',
		},
		{
			name: 'Game History',
			link: '/admin/game-history',
			icon: TabletIcon,
			permission: 'GetUsersInGame',
		},
		{
			name: "Logs",
			link: "/admin/logs",
			icon: BookOpenIcon,
			permission: "GetAdminLogs",
		},
		{
			name: "Create Player",
			link: "/admin/user/create",
			icon: UsersIcon,
			permission: "CreateUser",
		},
		{
			name: 'Text Moderation',
			link: '/admin/text-posts',
			icon: PhoneIcon,
			permission: 'GetAllAssetComments',
		},
		{
			name: 'Forums',
			link: '/admin/forums',
			icon: BookIcon,
			permission: 'LockForumThread',
		},
		{
			name: "Asset Moderation",
			link: "/admin/asset/approval",
			icon: CheckSquareIcon,
			permission: "GetPendingModerationItems",
		},
		{
			name: "Lottery",
			link: "/admin/lottery",
			icon: SunriseIcon,
			permission: "RunLottery",
		},
		{
			name: "Feature Flags",
			link: "/admin/feature-flags",
			icon: FlagIcon,
			permission: "ManageFeatureFlags",
		},
		{
			name: 'Applications',
			link: '/admin/applications',
			icon: TerminalIcon,
			permission: 'ManageApplications',
		},
		{
			name: 'Force Application',
			link: '/admin/force-application',
			icon: TerminalIcon,
			permission: 'ForceApplication',
		},
		{
			name: 'Reports',
			link: '/admin/reports',
			icon: StarIcon,
			permission: 'ManageReports',
		},
		{
			name: 'Resolve URL',
			link: '/admin/resolve-url',
			icon: TargetIcon,
			permission: 'GetDetailsFromThumbnail',
		},
	] as any[];

	let active: string = "/admin/";
	onMount(() => {
		if (typeof window !== "undefined") {
			active = window.location.pathname;
		}
	});
</script>

<nav id="sidebarMenu" class="col-md-3 col-lg-2 d-md-block bg-dark text-white sidebar collapse">
	<div class="sidebar-sticky pt-3">
		<h6 class="sidebar-heading d-flex justify-content-between align-items-center px-3 mt-0 mb-1 text-muted">
			<span>Saved pages</span>
			<a
				class="link-secondary"
				href="#!"
				on:click={(e) => {
					e.preventDefault();
					let title = document.title;
					if (title === "Admin" || !title) {
						title = "Untitled Page";
					}
					let url = location.pathname + (location.search || "");
					addPage(title, url);
				}}
			>
				<PlusCircleIcon size="16" />
			</a>
		</h6>
		<ul class="nav flex-column mb-2">
			{#if savedPages.length === 0}
				<ul class="nav flex-column mb-0">
					<li class="nav-item">
						<p class="pr-4 pl-4">You do not have any saved pages. Click the <PlusCircleIcon size="16" /> icon to save a page.</p>
					</li>
				</ul>
			{:else}
				{#each savedPages as p}
					<PageEntry title={p.title} url={p.url} />
				{/each}
			{/if}
		</ul>
		<ul class="nav flex-column">
			{#each navItems as item}
				{#if hasPerm(item.permission)}
					<li class="nav-item">
						<a use:link class={`nav-link${item.link === active ? " active" : ""}`} href={item.link}>
							<svelte:component this={item.icon} size="16" />
							{item.name}
						</a>
					</li>
				{/if}
			{/each}
			{#if hasPerm("CreateAsset") || hasPerm("SetAssetProduct") || hasPerm("MigrateAssetFromRoblox") || hasPerm("CreateAssetVersion") || hasPerm("RequestAssetReRender") || hasPerm('CreateBundleCopiedFromRoblox') || hasPerm('CreateAssetCopiedFromRoblox')}
				<h6 class="sidebar-heading d-flex justify-content-between align-items-center px-3 mt-2 mb-1 text-muted">
					<span style="color: white;">Catalog</span>
				</h6>
			{/if}
			{#if hasPerm("CreateAsset")}
				<li class="nav-item ml-4">
					<a use:link class="nav-link" href="/admin/asset/create"><FilePlusIcon size="16" /> Create Item</a>
				</li>
			{/if}
			{#if hasPerm("CreateAssetCopiedFromRoblox")}
				<li class="nav-item ml-4">
					<a use:link class="nav-link" href="/admin/asset/copy"><CopyIcon size="16" /> Copy Asset</a>
				</li>
			{/if}
			{#if hasPerm("CreateAssetCopiedFromRoblox")}
				<li class="nav-item ml-4">
					<a use:link class="nav-link" href="/admin/asset/ugccopy"><CopyIcon size="16" /> Copy UGC</a>
				</li>
			{/if}
			{#if hasPerm("CreateBundleCopiedFromRoblox")}
				<li class="nav-item ml-4">
					<a use:link class="nav-link" href="/admin/bundle/copy"><CopyIcon size="16" /> Copy Bundle</a>
				</li>
			{/if}
			{#if hasPerm("SetAssetProduct")}
				<li class="nav-item ml-4">
					<a use:link class="nav-link" href="/admin/product/update"><EditIcon size="16" /> Update Item Product</a>
				</li>
			{/if}

			{#if hasPerm("MigrateAssetFromRoblox")}
				<li class="nav-item ml-4">
					<a use:link class="nav-link" href="/admin/asset/create-for-item"><FolderPlusIcon size="16" /> Create Item Asset</a>
				</li>
			{/if}
			{#if hasPerm("CreateAssetVersion")}
				<li class="nav-item ml-4">
					<a use:link class="nav-link" href="/admin/asset/version/create"><UploadCloudIcon size="16" /> Update Item RBXM</a>
				</li>
				<li class="nav-item ml-4">
					<a use:link class="nav-link" href="/admin/giftallusers"><FolderPlusIcon size="16" /> Gift All Users</a>
				</li>
			{/if}
			{#if hasPerm("GiveUserItem")}
				<li class="nav-item ml-4">
					<a use:link class="nav-link" href="/admin/asset/track"><RefreshCcwIcon size="16" /> Tack User Assets</a>
				</li>
			{/if}
			{#if hasPerm("RequestAssetReRender")}
				<li class="nav-item ml-4">
					<a use:link class="nav-link" href="/admin/asset/re-render"><RefreshCcwIcon size="16" /> Force Item Re-Render</a>
				</li>
			{/if}
			<li class="nav-item mt-2 d-md-none d-block">
				<a class="nav-link" href="/">Back to Marine</a>
			</li>
		</ul>
	</div>
</nav>

<style>
	nav#sidebarMenu.sidebar {
		top: 0 !important;
	}
</style>

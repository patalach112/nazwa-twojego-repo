import { createUseStyles } from "react-jss";
import { getLogoStyle, logoStyle } from "../../../services/theme";
import NavigationStore from "../../../stores/navigation";
import AuthenticationStore from "../../../stores/authentication";

// Only these logo assets are bundled. Retain legacy preference names, but
// fall back instead of requesting images that do not exist in this checkout.
const faviconImages = {[logoStyle.Default]: 'url(/img/favicon.png)'};
const logoImages = {
    [logoStyle.Default]: 'url(/img/logo.png)',
    [logoStyle.Pekora]: 'url(/img/pekora-logo.png)',
};

const useLogoStyles = createUseStyles({
  imgDesktop: {
    width: '122px',
    minWidth: '122px',
    maxWidth: '122px',
    height: '40px',
    // backgroundImage: `url(/img/roblox_logo.svg)`,
    //backgroundImage: 'url(/img/holiday/projex_logo_studio.png)',
    // backgroundSize: '122px 30px',
    backgroundSize: "100% auto",
    display: 'none',
    '@media(min-width: 1325px)': {
      display: 'block',
    },
    backgroundRepeat: 'no-repeat',
    backgroundPosition: 'center'
  },
  imgMobile: {
    //backgroundImage: `url(/img/logo_R.svg)`,
    width: '30px',
    height: '30px',
    display: 'block',
    backgroundSize: '30px',
    backgroundRepeat: 'no-repeat',
    backgroundPosition: 'center',
    marginLeft: '6px',
    '@media(min-width: 1325px)': {
      display: 'none',
    },
  },
  imgMobileWrapper: {
    marginLeft: '12px',
  },
  col: {
    maxWidth: '118px',
    padding: '0',
    margin: '0 12px',
    display: 'flex',
    justifyContent: 'start',
    alignItems: 'center',
    '@media(max-width: 1324px)': {
      margin: '0 6px',
      width: 'auto',
    },
    '@media(max-width: 992px)': {
      width: '20%',
      margin: 0,
      marginBottom: '6px',
    },
  },
  openSideNavMobile: {
    display: 'none',
    border: 0,
    padding: 0,
    backgroundColor: 'transparent',
    '@media(max-width: 1324px)': {
      display: 'block',
      float: 'left',
      height: '30px',
      width: '30px',
      cursor: 'pointer',
    },
  },
});
const Logo = () => {
  const s = useLogoStyles();
  const logo = getLogoStyle();
  const navStore = NavigationStore.useContainer();
  const auth = AuthenticationStore.useContainer();

  return <div className={`${s.col} col-3 col-lg-3`}>
    {auth.isAuthenticated && <button type='button' aria-label='Open navigation' aria-expanded={navStore.isSidebarOpen} className={s.openSideNavMobile + ' icon-menu'} onClick={() => {
      navStore.setIsSidebarOpen(!navStore.isSidebarOpen);
    }} />}
    <a aria-label='Marine home' className={s.imgDesktop} style={{backgroundImage: logoImages[logo] || logoImages[logoStyle.Default]}} href='/'></a>
    <a aria-label='Marine home' className={s.imgMobile} style={{backgroundImage: faviconImages[logo] || faviconImages[logoStyle.Default]}} href='/'></a>
    {/*<div className={s.imgMobileWrapper}>
    
    </div>*/}
  </div>
}

export default Logo;
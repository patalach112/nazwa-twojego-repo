import {createUseStyles} from 'react-jss';

const useStyles = createUseStyles({
  loginContainer: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'flex-end',
    gap: '8px',
    width: 'auto',
    marginLeft: 'auto',
    marginRight: '12px',
    padding: 0,
    '@media(max-width: 991px)': {width: 'calc(60% - 18px)'},
    '@media(max-width: 400px)': {width: 'calc(68% - 18px)'},
  },
  link: {
    color: 'white',
    fontWeight: 400,
    fontSize: '16px',
    textDecoration: 'none',
    padding: '6px 9px',
    whiteSpace: 'nowrap',
    borderRadius: '3px',
    '&:hover': {color: 'white', background: 'rgba(25,25,25,0.1)'},
  },
  signupLink: {
    backgroundColor: '#02b757',
    '&:hover': {backgroundColor: '#3fc679'},
  },
});

const LoginArea = () => {
  const s = useStyles();
  // Account passwords and 2FA belong to the existing Razor login, not the
  // retired cookie-import screen or the disabled /apisite/auth/v2/login route.
  return <div className={s.loginContainer}>
    <a className={s.link} href='/auth/accountlogin'>Login</a>
    <a className={`${s.link} ${s.signupLink}`} href='/auth/signup'>Sign Up</a>
  </div>;
};

export default LoginArea;

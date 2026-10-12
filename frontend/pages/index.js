import {useEffect} from 'react';
import AuthenticationStore from '../stores/authentication';
import Theme2016 from '../components/theme2016';
import Games from '../components/gamesPage';
import GamesPageStore from '../stores/gamesPage';

const IndexPage = () => {
  const auth = AuthenticationStore.useContainer();
  useEffect(() => {
    if (auth.isAuthenticated) window.location.replace('/home');
  }, [auth.isAuthenticated]);

  return <Theme2016>
    {auth.isPending || auth.isAuthenticated ? <p className='container mt-4' role='status'>Loading your account...</p> : <>
      <main className='container py-4'>
        <h1>Welcome to Marine</h1>
        <p>Discover community games, build something new, and play with friends.</p>
        <div className='d-flex flex-wrap gap-2'>
          <a className='btn btn-primary' href='/auth/accountlogin'>Login</a>
          <a className='btn btn-success' href='/auth/signup'>Sign Up</a>
        </div>
      </main>
      <GamesPageStore.Provider><Games /></GamesPageStore.Provider>
    </>}
  </Theme2016>;
};

export default IndexPage;
